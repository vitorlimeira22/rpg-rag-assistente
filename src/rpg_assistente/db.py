import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    edition TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(title, edition)
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    pdf_page INTEGER NOT NULL,
    printed_page TEXT,
    chapter TEXT,
    section TEXT,
    text TEXT NOT NULL,
    UNIQUE(book_id, pdf_page)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE(page_id, position)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    model TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_usd REAL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vector_indices (
    book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    collection TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    house_rules TEXT NOT NULL DEFAULT '',
    system TEXT NOT NULL DEFAULT 'Ordem Paranormal',
    edition TEXT NOT NULL DEFAULT '1.1',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS campaign_events (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS campaign_events_campaign ON campaign_events(campaign_id, id);
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    character_class TEXT NOT NULL,
    data_json TEXT NOT NULL,
    system TEXT NOT NULL DEFAULT 'Ordem Paranormal',
    edition TEXT NOT NULL DEFAULT '1.1',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS llm_call_context (
    call_id INTEGER PRIMARY KEY REFERENCES llm_calls(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    source_ids TEXT NOT NULL,
    book_sha256 TEXT,
    prompt_version TEXT NOT NULL,
    retrieval_ms INTEGER NOT NULL,
    total_ms INTEGER,
    citation_status TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('consult', 'mission')),
    campaign_id INTEGER REFERENCES campaigns(id),
    book_sha256 TEXT NOT NULL,
    book_title TEXT NOT NULL,
    book_edition TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS conversation_requests (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed')),
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(conversation_id, request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS conversation_one_pending
    ON conversation_requests(conversation_id) WHERE status='pending';
CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(conversation_id, request_id, role),
    FOREIGN KEY(conversation_id, request_id)
        REFERENCES conversation_requests(conversation_id, request_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS conversation_messages_order
    ON conversation_messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS character_sheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_id INTEGER UNIQUE,
    revision INTEGER NOT NULL DEFAULT 1,
    data_json TEXT NOT NULL,
    book_sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sheet_requests (
    sheet_id INTEGER NOT NULL REFERENCES character_sheets(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','completed')),
    expires_at REAL NOT NULL,
    PRIMARY KEY(sheet_id,request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS sheet_one_pending
    ON sheet_requests(sheet_id) WHERE status='pending';
CREATE TABLE IF NOT EXISTS sheet_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id INTEGER NOT NULL REFERENCES character_sheets(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(sheet_id, request_id, role),
    FOREIGN KEY(sheet_id,request_id) REFERENCES sheet_requests(sheet_id,request_id)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def reserve_call(conn: sqlite3.Connection, model: str, daily_limit: int) -> int | None:
    """Reserva uma tentativa atomicamente; libera a transação antes da rede.

    Falhas e processos interrompidos consomem a reserva. O limite usa dias UTC.
    """
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        count = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE created_at >= date('now') AND status != 'skipped'"
        ).fetchone()[0]
        if count >= daily_limit:
            return None
        return conn.execute(
            "INSERT INTO llm_calls(model, elapsed_ms, status) VALUES (?, 0, 'pending')", (model,)
        ).lastrowid


def clear_books(conn: sqlite3.Connection) -> None:
    """O primeiro protótipo mantém somente um sistema e uma edição ativos."""
    conn.execute("DELETE FROM chunks_fts")
    conn.execute("DELETE FROM books")
