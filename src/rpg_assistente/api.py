from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
import pymupdf
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .answer import answer_question
from .campaigns import CampaignInput, EventInput, get_campaign
from .characters import CharacterInput, character_options, validate_character
from . import conversations as conversation_store
from .conversations import ConversationError, ConversationInput, MessageInput
from .config import get_settings
from .db import connect
from .search import retrieve
from .security import LocalSecurityMiddleware
from . import sheets as sheet_store
from .sheet_models import SheetData
from .sheets import SheetUpdate, SheetMessage
from .sheet_chat import generate_sheet_reply
from .sheet_pdf import sheet_pdf_info, export_sheet_pdf, SheetPdfError

STATIC = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)
app = FastAPI(title="Assistente de RPG · Ordem Paranormal 1.1", version="0.3.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.add_middleware(LocalSecurityMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])


@contextmanager
def database(settings=None):
    conn = connect((settings or get_settings()).db_path)
    try:
        yield conn
    finally:
        conn.close()


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=2000)
    mode: Literal["consult", "mission"] = "consult"
    campaign_id: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("question")
    @classmethod
    def trim_question(cls, value: str) -> str:
        if len(value.strip()) < 3:
            raise ValueError("Escreva um pedido com pelo menos três caracteres.")
        return value.strip()


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(Exception)
async def failed_request(request: Request, exc: Exception):
    logger.error("Falha no serviço local (%s)", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Não foi possível concluir a operação no serviço local."})


@app.exception_handler(ConversationError)
async def conversation_error(request: Request, exc: ConversationError):
    return JSONResponse(status_code=exc.status_code, content={
        "detail": {"code": exc.code, "message": exc.message},
    })


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/books")
def books() -> list[dict]:
    with database() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id, title, edition, page_count, imported_at FROM books ORDER BY imported_at DESC"
        )]


@app.get("/api/status")
def service_status() -> dict:
    settings = get_settings()
    with database(settings) as conn:
        row = conn.execute("SELECT id, title, edition, page_count FROM books LIMIT 1").fetchone()
        index = conn.execute("SELECT collection, chunk_count FROM vector_indices LIMIT 1").fetchone()
        used = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE created_at >= date('now') AND status != 'skipped'"
        ).fetchone()[0]
    warnings = []
    if not row:
        warnings.append("Importe o livro pelo terminal para habilitar a consulta.")
    if row and not index:
        warnings.append("O índice vetorial ainda não foi preparado; a busca textual está disponível.")
    if row and not settings.allow_private_book_to_groq:
        warnings.append("O envio de livros privados ao Groq está desabilitado.")
    return {"book": dict(row) if row else None, "llm_configured": bool(settings.groq_api_key and settings.groq_model),
            "model": settings.groq_model, "vector_ready": bool(index),
            "reranker_enabled": settings.rerank_enabled, "used_calls": used,
            "max_calls": settings.max_calls_per_day, "warnings": warnings}


@app.post("/api/search")
def find(request: Question) -> dict:
    settings = get_settings()
    with database(settings) as conn:
        book = conn.execute("SELECT sha256 FROM books ORDER BY id LIMIT 1").fetchone()
        result = retrieve(conn, request.question, settings)
        return {**result, "book_sha256": book["sha256"] if book else None}


@app.post("/api/ask")
def ask(request: Question) -> dict:
    settings = get_settings()
    with database(settings) as conn:
        if request.campaign_id is not None and not get_campaign(conn, request.campaign_id):
            raise HTTPException(404, "Campanha não encontrada.")
        try:
            return answer_question(conn, request.question, settings, mode=request.mode, campaign_id=request.campaign_id)
        except Exception as exc:
            logger.error("Falha na consulta (%s)", type(exc).__name__)
            raise HTTPException(502, "Não foi possível concluir a consulta. Confira o serviço local.") from exc


@app.get("/api/sources/{chunk_id}")
def get_source(chunk_id: int, book_sha256: str | None = None) -> dict:
    with database() as conn:
        row = conn.execute(
            "SELECT c.id,c.text,p.pdf_page,p.printed_page,p.chapter,p.section,b.title,b.edition,b.sha256 "
            "FROM chunks c JOIN pages p ON p.id=c.page_id JOIN books b ON b.id=p.book_id WHERE c.id=?",
            (chunk_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Trecho não encontrado.")
    _check_book_hash(row, book_sha256)
    item = dict(row)
    item.pop("sha256")
    return item


def _check_book_hash(book, expected_hash: str | None) -> None:
    if expected_hash is not None and book["sha256"] != expected_hash:
        raise HTTPException(409, "Esta fonte pertence a outro livro. O histórico foi preservado.")


@lru_cache(maxsize=4)
def _pdf_hash(path: str, size: int, mtime_ns: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _render_page(book: dict, pdf_page: int) -> Response:
    path = Path(book["pdf_path"])
    if not path.is_file():
        raise HTTPException(404, "PDF original não está mais no caminho importado.")
    stat = path.stat()
    if _pdf_hash(str(path), stat.st_size, stat.st_mtime_ns) != book["sha256"]:
        raise HTTPException(409, "O PDF original mudou. Reimporte e reconstrua o índice antes de conferir as fontes.")
    with pymupdf.open(path) as document:
        if not 1 <= pdf_page <= len(document):
            raise HTTPException(404, "Página não encontrada.")
        png = document[pdf_page - 1].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).tobytes("png")
    return Response(content=png, media_type="image/png")


@app.get("/api/sources/{chunk_id}/page.png")
def page_image(chunk_id: int, book_sha256: str | None = None) -> Response:
    with database() as conn:
        row = conn.execute(
            "SELECT b.pdf_path, b.sha256, p.pdf_page FROM chunks c JOIN pages p ON p.id=c.page_id "
            "JOIN books b ON b.id=p.book_id WHERE c.id=?", (chunk_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Trecho não encontrado.")
    _check_book_hash(row, book_sha256)
    return _render_page(dict(row), row["pdf_page"])


@app.get("/api/book/pages/{pdf_page}/image")
def book_page(pdf_page: int, book_sha256: str | None = None) -> Response:
    with database() as conn:
        row = conn.execute("SELECT pdf_path, sha256 FROM books LIMIT 1").fetchone()
    if row is None:
        raise HTTPException(404, "Nenhum livro importado.")
    _check_book_hash(row, book_sha256)
    return _render_page(dict(row), pdf_page)


@app.get("/api/conversations")
def conversations() -> list[dict]:
    with database() as conn:
        return conversation_store.list_conversations(conn)


@app.post("/api/conversations", status_code=201)
def create_conversation(payload: ConversationInput) -> dict:
    with database() as conn:
        return conversation_store.create_conversation(conn, payload)


@app.get("/api/conversations/{conversation_id}")
def read_conversation(conversation_id: int) -> dict:
    with database() as conn:
        return conversation_store.get_conversation(conn, conversation_id)


@app.post("/api/conversations/{conversation_id}/messages")
def send_conversation_message(conversation_id: int, payload: MessageInput) -> dict:
    settings = get_settings()
    with database(settings) as conn:
        return conversation_store.send_message(conn, conversation_id, payload, settings, answer_question)


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int) -> Response:
    with database() as conn:
        conversation_store.delete_conversation(conn, conversation_id)
    return Response(status_code=204)


@app.get("/api/campaigns")
def campaigns() -> list[dict]:
    with database() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM campaigns ORDER BY id DESC")]


@app.post("/api/campaigns", status_code=201)
def create_campaign(payload: CampaignInput) -> dict:
    with database() as conn, conn:
        campaign_id = conn.execute(
            "INSERT INTO campaigns(name, summary, house_rules) VALUES (?, ?, ?)",
            (payload.name, payload.summary, payload.house_rules),
        ).lastrowid
        return get_campaign(conn, campaign_id)


@app.get("/api/campaigns/{campaign_id}")
def read_campaign(campaign_id: int) -> dict:
    with database() as conn:
        result = get_campaign(conn, campaign_id)
    if result is None:
        raise HTTPException(404, "Campanha não encontrada.")
    return result


@app.put("/api/campaigns/{campaign_id}")
def update_campaign(campaign_id: int, payload: CampaignInput) -> dict:
    with database() as conn, conn:
        changed = conn.execute(
            "UPDATE campaigns SET name=?, summary=?, house_rules=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (payload.name, payload.summary, payload.house_rules, campaign_id),
        ).rowcount
        if not changed:
            raise HTTPException(404, "Campanha não encontrada.")
        return get_campaign(conn, campaign_id)


@app.post("/api/campaigns/{campaign_id}/events", status_code=201)
def add_event(campaign_id: int, payload: EventInput) -> dict:
    with database() as conn, conn:
        if not conn.execute("SELECT 1 FROM campaigns WHERE id=?", (campaign_id,)).fetchone():
            raise HTTPException(404, "Campanha não encontrada.")
        event_id = conn.execute(
            "INSERT INTO campaign_events(campaign_id,text) VALUES (?,?)", (campaign_id, payload.text)
        ).lastrowid
        return dict(conn.execute("SELECT id,text,created_at FROM campaign_events WHERE id=?", (event_id,)).fetchone())


@app.get("/api/rules/character-options")
def options() -> dict:
    return character_options()


@app.post("/api/characters/validate")
def check_character(payload: CharacterInput) -> dict:
    return validate_character(payload)


def _character_record(row) -> dict:
    data = json.loads(row["data_json"])
    return {**data, "id": row["id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "validation": validate_character(data)}


@app.get("/api/characters")
def characters() -> list[dict]:
    with database() as conn:
        return [_character_record(row) for row in conn.execute("SELECT * FROM characters ORDER BY id DESC")]


@app.get("/api/characters/{character_id}")
def read_character(character_id: int) -> dict:
    with database() as conn:
        row = conn.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Ficha não encontrada.")
    return _character_record(row)


def _save_character(payload: CharacterInput, character_id: int | None = None) -> dict:
    validation = validate_character(payload)
    if not validation["valid"]:
        raise HTTPException(422, {"message": "Corrija os campos da ficha básica.", "validation": validation})
    data = payload.model_dump_json()
    with database() as conn, conn:
        if character_id is None:
            character_id = conn.execute(
                "INSERT INTO characters(name,character_class,data_json) VALUES(?,?,?)",
                (payload.name, payload.character_class, data),
            ).lastrowid
        else:
            changed = conn.execute(
                "UPDATE characters SET name=?,character_class=?,data_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (payload.name, payload.character_class, data, character_id),
            ).rowcount
            if not changed:
                raise HTTPException(404, "Ficha não encontrada.")
        return _character_record(conn.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone())


@app.post("/api/characters", status_code=201)
def create_character(payload: CharacterInput) -> dict:
    return _save_character(payload)


@app.put("/api/characters/{character_id}")
def update_character(character_id: int, payload: CharacterInput) -> dict:
    return _save_character(payload, character_id)


def _sheet_export_state(record, settings):
    record["pdf_available"] = sheet_pdf_info(settings.sheet_pdf_template)["available"]
    return record


@app.get("/api/sheets")
def sheets() -> list[dict]:
    with database() as conn:
        return sheet_store.list_sheets(conn)


@app.post("/api/sheets", status_code=201)
def create_sheet(payload: SheetData) -> dict:
    settings = get_settings()
    with database(settings) as conn:
        return _sheet_export_state(sheet_store.create_sheet(conn, payload), settings)


@app.get("/api/sheets/{sheet_id}")
def read_sheet(sheet_id: int) -> dict:
    settings = get_settings()
    with database(settings) as conn:
        return _sheet_export_state(sheet_store.get_sheet(conn, sheet_id), settings)


@app.put("/api/sheets/{sheet_id}")
def update_sheet(sheet_id: int, payload: SheetUpdate) -> dict:
    settings = get_settings()
    with database(settings) as conn:
        return _sheet_export_state(sheet_store.update_sheet(conn, sheet_id, payload), settings)


@app.delete("/api/sheets/{sheet_id}", status_code=204)
def delete_sheet(sheet_id: int) -> Response:
    with database() as conn:
        sheet_store.delete_sheet(conn, sheet_id)
    return Response(status_code=204)


@app.post("/api/sheets/{sheet_id}/chat")
def sheet_chat(sheet_id: int, payload: SheetMessage) -> dict:
    settings = get_settings()
    with database(settings) as conn:
        response = sheet_store.send_sheet_message(conn, sheet_id, payload, settings, generate_sheet_reply)
        _sheet_export_state(response["sheet"], settings)
        return response


@app.get("/api/sheets/{sheet_id}/pdf")
def sheet_pdf(sheet_id: int) -> Response:
    settings = get_settings()
    with database(settings) as conn:
        sheet = sheet_store.get_sheet(conn, sheet_id)
    if settings.sheet_pdf_template is None:
        raise HTTPException(409, "Configure SHEET_PDF_TEMPLATE com o caminho do modelo editável no .env.")
    try:
        document = export_sheet_pdf(sheet["data"], settings.sheet_pdf_template)
    except SheetPdfError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(document, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="agente-{sheet_id}.pdf"',
    })
