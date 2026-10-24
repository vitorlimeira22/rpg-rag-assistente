"""Persistência de fichas e chat com idempotência e revisão otimista.

Não há transação aberta durante inferência. Uma resposta baseada numa revisão
antiga nunca substitui uma edição mais recente, inclusive de outra aba.
"""
from __future__ import annotations

from hashlib import sha256
import json
import time
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

from .conversations import ConversationError, _redact
from .sheet_models import SheetData, sheet_payload


class SheetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(gt=0, strict=True)
    data: SheetData


class SheetMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(gt=0, strict=True)
    request_id: UUID
    content: str = Field(min_length=1, max_length=2000)


def _error(status, code, text):
    return ConversationError(status, code, text)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _row(conn, sheet_id):
    row = conn.execute("SELECT * FROM character_sheets WHERE id=?", (sheet_id,)).fetchone()
    if row is None:
        raise _error(404, "sheet_not_found", "Ficha não encontrada.")
    return row


def _book_hash(conn):
    book = conn.execute("SELECT sha256 FROM books ORDER BY id LIMIT 1").fetchone()
    return book["sha256"] if book else None


def migrate_legacy(conn):
    """Copia fichas antigas uma vez; não altera nem exclui os registros originais."""
    with conn:
        for row in conn.execute("SELECT * FROM characters WHERE id NOT IN (SELECT legacy_id FROM character_sheets WHERE legacy_id IS NOT NULL)").fetchall():
            sheet = SheetData.model_validate(json.loads(row["data_json"]))
            conn.execute("INSERT OR IGNORE INTO character_sheets(legacy_id,data_json,book_sha256) VALUES (?,?,?)",
                         (row["id"], _json(sheet.model_dump()), _book_hash(conn)))


def list_sheets(conn):
    migrate_legacy(conn)
    return [{"id": row["id"], "revision": row["revision"], "updated_at": row["updated_at"],
             **{key: json.loads(row["data_json"])[key] for key in ("name", "character_class", "nex")}}
            for row in conn.execute("SELECT * FROM character_sheets ORDER BY updated_at DESC,id DESC")]


def get_sheet(conn, sheet_id):
    row = _row(conn, sheet_id)
    data, report = sheet_payload(SheetData.model_validate(json.loads(row["data_json"])))
    messages = []
    for item in conn.execute("SELECT * FROM sheet_messages WHERE sheet_id=? ORDER BY id", (sheet_id,)):
        message = {key: item[key] for key in ("id", "role", "content", "request_id", "created_at")}
        if item["result_json"]:
            message["result"] = json.loads(item["result_json"])
        messages.append(message)
    pending = conn.execute("SELECT request_id,expires_at FROM sheet_requests WHERE sheet_id=? AND status='pending'", (sheet_id,)).fetchone()
    return {"id": sheet_id, "revision": row["revision"], "data": data, "validation": report,
            "messages": messages, "updated_at": row["updated_at"],
            "book_matches": row["book_sha256"] in (None, _book_hash(conn)),
            "pending_request_id": pending["request_id"] if pending else None,
            "pending_expired": bool(pending and pending["expires_at"] <= time.time())}


def create_sheet(conn, data: SheetData):
    with conn:
        sheet_id = conn.execute("INSERT INTO character_sheets(data_json,book_sha256) VALUES (?,?)",
                                (_json(data.model_dump()), _book_hash(conn))).lastrowid
    return get_sheet(conn, sheet_id)


def update_sheet(conn, sheet_id, payload: SheetUpdate):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _row(conn, sheet_id)
        changed = conn.execute("UPDATE character_sheets SET data_json=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND revision=?",
                               (_json(payload.data.model_dump()), sheet_id, payload.revision)).rowcount
        if not changed:
            raise _error(409, "sheet_conflict", "Esta ficha foi alterada em outra operação. Recarregue a ficha antes de salvar; suas alterações locais não foram enviadas.")
    return get_sheet(conn, sheet_id)


def delete_sheet(conn, sheet_id):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _row(conn, sheet_id)
        pending = conn.execute("SELECT 1 FROM sheet_requests WHERE sheet_id=? AND status='pending' AND expires_at>?", (sheet_id, time.time())).fetchone()
        if pending:
            raise _error(409, "sheet_busy", "Aguarde a resposta do chat antes de excluir a ficha.")
        # Mantém a origem da migração para que a exclusão não ressuscite a ficha.
        legacy = _row(conn, sheet_id)["legacy_id"]
        if legacy is not None:
            conn.execute("DELETE FROM characters WHERE id=?", (legacy,))
        conn.execute("DELETE FROM character_sheets WHERE id=?", (sheet_id,))


def _finish(conn, sheet_id, request_id, result):
    conn.execute("INSERT INTO sheet_messages(sheet_id,request_id,role,content,result_json) VALUES (?,?,'assistant',?,?)",
                 (sheet_id, request_id, result["answer"], _json(result)))
    conn.execute("UPDATE sheet_requests SET status='completed' WHERE sheet_id=? AND request_id=?", (sheet_id, request_id))


def send_sheet_message(conn, sheet_id, payload: SheetMessage, settings, generator):
    request_id = str(payload.request_id)
    fingerprint = sha256(_json([payload.revision, payload.content]).encode()).hexdigest()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _row(conn, sheet_id)
        previous = conn.execute("SELECT * FROM sheet_requests WHERE sheet_id=? AND request_id=?", (sheet_id, request_id)).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise _error(409, "request_mismatch", "O identificador deste envio já foi usado com outros dados.")
            if previous["status"] == "pending" and previous["expires_at"] <= time.time():
                _finish(conn, sheet_id, request_id, {"answer": "A resposta foi interrompida. Nenhuma alteração foi aplicada. Envie outra mensagem para continuar.", "status": "interrupted", "sources": []})
            elif previous["status"] == "pending":
                raise _error(409, "sheet_busy", "Esta mensagem ainda está sendo processada. Aguarde e atualize a ficha.")
            record = get_sheet(conn, sheet_id)
            return {"sheet": record, "messages": record["messages"]}
        pending = conn.execute("SELECT * FROM sheet_requests WHERE sheet_id=? AND status='pending'", (sheet_id,)).fetchone()
        if pending:
            if pending["expires_at"] > time.time():
                raise _error(409, "sheet_busy", "Aguarde a resposta anterior desta ficha.")
            _finish(conn, sheet_id, pending["request_id"], {"answer": "A resposta anterior foi interrompida; a ficha foi preservada.", "status": "interrupted", "sources": []})
        if row["revision"] != payload.revision:
            raise _error(409, "sheet_conflict", "A ficha mudou. Recarregue antes de enviar sua mensagem.")
        book_hash = _book_hash(conn)
        if not book_hash:
            raise _error(409, "book_unavailable", "Importe o livro antes de usar o assistente da ficha.")
        if row["book_sha256"] not in (None, book_hash):
            raise _error(409, "book_changed", "O livro desta ficha foi substituído. A edição manual permanece disponível; crie outra ficha para o livro atual.")
        content = _redact(payload.content, settings.groq_api_key)
        history = [{"role": m["role"], "content": m["content"][:1200]}
                   for m in conn.execute("SELECT role,content FROM sheet_messages WHERE sheet_id=? ORDER BY id DESC LIMIT 6", (sheet_id,))][::-1]
        last = conn.execute("SELECT result_json FROM sheet_messages WHERE sheet_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",(sheet_id,)).fetchone()
        previous_result = json.loads(last["result_json"]) if last and last["result_json"] else {}
        if (previous_result.get("base_revision") == row["revision"]
                and previous_result.get("creation_task")
                and previous_result.get("status") not in {"draft","answered"}):
            history.append({"role":"assistant","content":"Criação anterior ainda pendente.",
                            "creation_task":previous_result["creation_task"]})
        conn.execute("INSERT INTO sheet_requests VALUES (?,?,?,?,'pending',?)",
                     (sheet_id, request_id, fingerprint, payload.revision, time.time() + max(180, settings.timeout_seconds * 2 + 60)))
        conn.execute("INSERT INTO sheet_messages(sheet_id,request_id,role,content) VALUES (?,?,'user',?)", (sheet_id, request_id, content))
    try:
        result = generator(conn, content, SheetData.model_validate(json.loads(row["data_json"])), history, settings)
        result = _redact(result, settings.groq_api_key)
        result["base_revision"] = payload.revision
        if not isinstance(result.get("answer"), str):
            raise ValueError("Resposta inválida")
        proposal = result.pop("proposal", None)
        parsed = SheetData.model_validate(proposal) if proposal is not None else None
    except Exception:
        parsed = None
        result = {"answer": "Não foi possível concluir a resposta. Sua ficha foi preservada; tente novamente com uma nova mensagem.", "status": "api_error", "sources": []}
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _row(conn, sheet_id)
        request = conn.execute("SELECT status FROM sheet_requests WHERE sheet_id=? AND request_id=?", (sheet_id, request_id)).fetchone()
        if request["status"] != "completed":
            if _book_hash(conn) != book_hash:
                parsed = None
                result = {"answer": "O livro mudou durante a consulta. A resposta foi descartada e a ficha preservada.", "status": "book_changed", "sources": []}
            elif current["revision"] != payload.revision:
                parsed = None
                result["answer"] = "A ficha foi editada durante a consulta. A proposta não foi aplicada para preservar sua edição.\n\n" + result["answer"]
                result["status"] = "conflict"
            if parsed is not None:
                conn.execute("UPDATE character_sheets SET data_json=?,revision=revision+1,book_sha256=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (_json(parsed.model_dump()), book_hash, sheet_id))
                result["sheet_updated"] = True
            else:
                result["sheet_updated"] = False
            _finish(conn, sheet_id, request_id, result)
    record = get_sheet(conn, sheet_id)
    return {"sheet": record, "messages": record["messages"], "result": result}
