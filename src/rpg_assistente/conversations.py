"""Conversas locais, snapshots de fontes e uma reserva por envio.

O histórico é contexto de diálogo, não uma nova fonte de regras. A reserva é
confirmada antes da geração; não há transação SQLite aberta durante a chamada.
Repetir um UUID nunca repete automaticamente uma tentativa já iniciada.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import logging
import sqlite3
from threading import Lock
import time
from typing import Callable, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings


logger = logging.getLogger(__name__)
_active_guard = Lock()
_active_conversations: set[tuple[str, int]] = set()


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Nova conversa", min_length=1, max_length=100)
    mode: Literal["consult", "mission"] = "consult"
    campaign_id: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Informe um título para a conversa.")
        return value.strip()


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=2000)
    request_id: UUID

    @field_validator("content")
    @classmethod
    def trim_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Escreva uma mensagem.")
        return value.strip()


class ConversationError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _pending_error() -> ConversationError:
    return ConversationError(
        409, "request_in_progress",
        "Uma resposta desta conversa ainda está em andamento. Aguarde e tente consultar novamente.",
    )


def _book(conn: sqlite3.Connection):
    return conn.execute("SELECT sha256,title,edition FROM books ORDER BY id LIMIT 1").fetchone()


def _book_matches(conversation, book) -> bool:
    return bool(book) and (
        conversation["book_sha256"], conversation["book_title"], conversation["book_edition"]
    ) == (book["sha256"], book["title"], book["edition"])


def _conversation(conn: sqlite3.Connection, conversation_id: int):
    row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if row is None:
        raise ConversationError(404, "conversation_not_found", "Conversa não encontrada.")
    return row


def _message(row) -> dict:
    result = {
        "id": row["id"], "role": row["role"], "content": row["content"],
        "request_id": row["request_id"], "created_at": row["created_at"],
    }
    if row["result_json"] is not None:
        result["result"] = json.loads(row["result_json"])
    return result


def _pair(conn: sqlite3.Connection, conversation_id: int, request_id: str) -> dict:
    messages = conn.execute(
        "SELECT * FROM conversation_messages WHERE conversation_id=? AND request_id=? ORDER BY id",
        (conversation_id, request_id),
    ).fetchall()
    by_role = {row["role"]: _message(row) for row in messages}
    return {"user_message": by_role["user"], "assistant_message": by_role["assistant"]}


def _record(conn: sqlite3.Connection, row, *, messages: bool = False) -> dict:
    record = dict(row)
    record["book_matches"] = _book_matches(row, _book(conn))
    pending = conn.execute(
        "SELECT request_id,expires_at FROM conversation_requests WHERE conversation_id=? AND status='pending'",
        (row["id"],),
    ).fetchone()
    record["pending_request_id"] = pending["request_id"] if pending else None
    record["pending_expired"] = bool(pending and pending["expires_at"] <= time.time())
    if messages:
        record["messages"] = [_message(item) for item in conn.execute(
            "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY id", (row["id"],),
        )]
    return record


def list_conversations(conn: sqlite3.Connection) -> list[dict]:
    return [_record(conn, row) for row in conn.execute(
        "SELECT * FROM conversations ORDER BY updated_at DESC,id DESC",
    )]


def get_conversation(conn: sqlite3.Connection, conversation_id: int) -> dict:
    return _record(conn, _conversation(conn, conversation_id), messages=True)


def create_conversation(conn: sqlite3.Connection, payload: ConversationInput) -> dict:
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        book = _book(conn)
        if book is None:
            raise ConversationError(409, "book_unavailable", "Importe um livro antes de iniciar uma conversa.")
        if payload.campaign_id is not None and not conn.execute(
            "SELECT 1 FROM campaigns WHERE id=?", (payload.campaign_id,),
        ).fetchone():
            raise ConversationError(404, "campaign_not_found", "Campanha não encontrada.")
        conversation_id = conn.execute(
            "INSERT INTO conversations(title,mode,campaign_id,book_sha256,book_title,book_edition) "
            "VALUES (?,?,?,?,?,?)",
            (payload.title, payload.mode, payload.campaign_id, book["sha256"], book["title"], book["edition"]),
        ).lastrowid
    return get_conversation(conn, conversation_id)


@contextmanager
def _exclusive(conn: sqlite3.Connection, conversation_id: int):
    # A trava em memória também protege uma operação excepcionalmente lenta
    # contra expiração da reserva no mesmo servidor. SQLite coordena conexões.
    database = conn.execute("PRAGMA database_list").fetchone()["file"] or f":memory:{id(conn)}"
    key = (database, conversation_id)
    with _active_guard:
        if key in _active_conversations:
            raise _pending_error()
        _active_conversations.add(key)
    try:
        yield
    finally:
        with _active_guard:
            _active_conversations.discard(key)


def _safe_result(status: str, message: str, conversation, *, usage=None) -> dict:
    return {
        "status": status, "answer": message, "mode": conversation["mode"],
        "sources": [], "warnings": [], "usage": usage,
        "book_sha256": conversation["book_sha256"],
    }


def _complete(conn: sqlite3.Connection, conversation, request_id: str, result: dict) -> dict:
    conn.execute(
        "INSERT INTO conversation_messages(conversation_id,request_id,role,content,result_json) "
        "VALUES (?,?,'assistant',?,?)",
        (conversation["id"], request_id, result["answer"],
         json.dumps(result, ensure_ascii=False, allow_nan=False)),
    )
    conn.execute(
        "UPDATE conversation_requests SET status='completed' WHERE conversation_id=? AND request_id=?",
        (conversation["id"], request_id),
    )
    conn.execute(
        "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation["id"],),
    )
    return _pair(conn, conversation["id"], request_id)


def _interrupt(conn: sqlite3.Connection, conversation, request_id: str) -> dict:
    return _complete(conn, conversation, request_id, _safe_result(
        "interrupted",
        "A resposta anterior foi interrompida e não foi repetida automaticamente. "
        "A tentativa pode ter consumido cota. Envie uma nova mensagem para tentar novamente.",
        conversation,
    ))


def _history(conn: sqlite3.Connection, conversation_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT m.role,m.content FROM conversation_messages m JOIN conversation_requests r "
        "ON r.conversation_id=m.conversation_id AND r.request_id=m.request_id "
        "WHERE m.conversation_id=? AND r.status='completed' ORDER BY m.id DESC LIMIT 8",
        (conversation_id,),
    ).fetchall()
    items = [{"role": row["role"], "content": row["content"][:4000 if row["role"] == "assistant" else 2000]}
             for row in reversed(rows)]
    # As mensagens completas são pares; não enviar uma resposta antiga solta.
    while len(items) > 2 and sum(len(item["content"]) for item in items) > 6000:
        items = items[2:]
    return items


def _fingerprint(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _existing(conn: sqlite3.Connection, conversation_id: int, payload: MessageInput):
    row = conn.execute(
        "SELECT * FROM conversation_requests WHERE conversation_id=? AND request_id=?",
        (conversation_id, str(payload.request_id)),
    ).fetchone()
    if row and row["content_sha256"] != _fingerprint(payload.content):
        raise ConversationError(
            409, "request_id_conflict", "Este identificador já foi usado para outra mensagem.",
        )
    return row


def _redact(value, secret: str):
    """Nunca persistir uma credencial conhecida que apareça em saída acidental."""
    if isinstance(value, str):
        return value.replace(secret, "[credencial removida]") if len(secret) >= 8 else value
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, secret) for key, item in value.items()
                if key.lower() not in {"api_key", "authorization", "headers", "pdf_path"}}
    return value


def _begin(conn: sqlite3.Connection, conversation_id: int, payload: MessageInput, settings: Settings):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conversation = _conversation(conn, conversation_id)
        previous = _existing(conn, conversation_id, payload)
        if previous:
            if previous["status"] == "completed":
                return _pair(conn, conversation_id, str(payload.request_id))
            if previous["expires_at"] <= time.time():
                return _interrupt(conn, conversation, str(payload.request_id))
            raise _pending_error()
        pending = conn.execute(
            "SELECT request_id,expires_at FROM conversation_requests WHERE conversation_id=? AND status='pending'",
            (conversation_id,),
        ).fetchone()
        if pending:
            if pending["expires_at"] > time.time():
                raise _pending_error()
            _interrupt(conn, conversation, pending["request_id"])
        if not _book_matches(conversation, _book(conn)):
            raise ConversationError(
                409, "book_changed",
                "O livro ativo mudou. O histórico foi preservado; inicie outra conversa para o livro atual.",
            )
        if conversation["campaign_id"] is not None and not conn.execute(
            "SELECT 1 FROM campaigns WHERE id=?", (conversation["campaign_id"],),
        ).fetchone():
            raise ConversationError(409, "campaign_unavailable", "A campanha desta conversa não está disponível.")
        history = _history(conn, conversation_id)
        # O estado é extraído de pedidos do usuário pela camada de diálogo.
        # Mantê-lo fora da janela textual evita perder classe/NEX ao truncar
        # respostas longas. O hash da conversa já foi conferido acima.
        previous_result = conn.execute(
            "SELECT m.result_json FROM conversation_messages m JOIN conversation_requests r "
            "ON r.conversation_id=m.conversation_id AND r.request_id=m.request_id "
            "WHERE m.conversation_id=? AND m.role='assistant' AND r.status='completed' "
            "ORDER BY m.id DESC LIMIT 1", (conversation_id,),
        ).fetchone()
        task_state = (json.loads(previous_result["result_json"]).get("task")
                      if previous_result and previous_result["result_json"] else None)
        # Prazo de recuperação conservador: o fluxo de pesquisa tem 120 s.
        # Um pedido expirado vira mensagem de interrupção, nunca uma nova chamada.
        expires_at = time.time() + 2 * max(120, settings.timeout_seconds) + 60
        conn.execute(
            "INSERT INTO conversation_requests(conversation_id,request_id,content_sha256,status,expires_at) "
            "VALUES (?,?,?,'pending',?)",
            (conversation_id, str(payload.request_id), _fingerprint(payload.content), expires_at),
        )
        content = _redact(payload.content, settings.groq_api_key)
        conn.execute(
            "INSERT INTO conversation_messages(conversation_id,request_id,role,content) VALUES (?,?,'user',?)",
            (conversation_id, str(payload.request_id), content),
        )
        title = content[:80] if conversation["title"] == "Nova conversa" else conversation["title"]
        conn.execute(
            "UPDATE conversations SET title=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (title, conversation_id),
        )
    return conversation, content, history, task_state


def send_message(conn: sqlite3.Connection, conversation_id: int, payload: MessageInput,
                 settings: Settings, answerer: Callable) -> dict:
    # Um replay já concluído não precisa esperar uma mensagem posterior.
    # As duas leituras compartilham um snapshot, inclusive se outro pedido
    # excluir a conversa. A transação termina antes de iniciar uma geração.
    with conn:
        conn.execute("BEGIN")
        previous = _existing(conn, conversation_id, payload)
        if previous and previous["status"] == "completed":
            return _pair(conn, conversation_id, str(payload.request_id))
    with _exclusive(conn, conversation_id):
        started = _begin(conn, conversation_id, payload, settings)
        if isinstance(started, dict):
            return started
        conversation, content, history, task_state = started
        try:
            result = answerer(
                conn, content, settings, mode=conversation["mode"],
                campaign_id=conversation["campaign_id"], history=history,
                **({"task_state": task_state} if task_state else {}),
            )
            result = _redact(result, settings.groq_api_key)
            if not isinstance(result, dict) or not isinstance(result.get("answer"), str):
                raise ValueError("Formato interno de resposta inválido.")
            result["mode"] = conversation["mode"]
            result["book_sha256"] = conversation["book_sha256"]
            # Valida serialização antes de abrir a transação de persistência.
            json.dumps(result, ensure_ascii=False, allow_nan=False)
        except Exception as exc:
            logger.error("Falha ao responder à conversa (%s)", type(exc).__name__)
            result = _safe_result(
                "api_error", "Não foi possível concluir esta resposta. Envie uma nova mensagem para tentar novamente.",
                conversation,
            )
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            _conversation(conn, conversation_id)
            if not _book_matches(conversation, _book(conn)):
                result = _safe_result(
                    "book_changed",
                    "O livro foi substituído durante a consulta. A resposta foi descartada para não misturar fontes. "
                    "Inicie outra conversa para consultar o livro atual.",
                    conversation, usage=result.get("usage"),
                )
            request = _existing(conn, conversation_id, payload)
            # Outro servidor pode ter recuperado uma reserva expirada. Sua
            # mensagem prevalece; nunca inserir uma segunda resposta para o UUID.
            if request["status"] == "completed":
                return _pair(conn, conversation_id, str(payload.request_id))
            return _complete(conn, conversation, str(payload.request_id), result)


def delete_conversation(conn: sqlite3.Connection, conversation_id: int) -> None:
    with _exclusive(conn, conversation_id), conn:
        conn.execute("BEGIN IMMEDIATE")
        _conversation(conn, conversation_id)
        pending = conn.execute(
            "SELECT expires_at FROM conversation_requests WHERE conversation_id=? AND status='pending'",
            (conversation_id,),
        ).fetchone()
        if pending and pending["expires_at"] > time.time():
            raise _pending_error()
        conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
