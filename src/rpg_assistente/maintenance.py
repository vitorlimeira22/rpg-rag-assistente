"""Atualização explícita da extração, sem substituir o livro ou dados do usuário."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pymupdf

from .ingest import _chunks
from .pdf_text import extract_page_text


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def refresh_book_text(conn: sqlite3.Connection) -> dict:
    """Reextrai o mesmo PDF; execute com o servidor parado e backup do banco.

    Conserva livro/páginas e IDs de chunks por posição nas páginas alteradas.
    Histórico mantém seus snapshots, sem reescrever respostas antigas. Índices
    vetoriais são invalidados na mesma transação: não usar embeddings obsoletos.
    """
    book = conn.execute("SELECT * FROM books ORDER BY id LIMIT 1").fetchone()
    if book is None:
        raise ValueError("Importe um PDF antes de atualizar a extração.")
    path = Path(book["pdf_path"])
    if _file_hash(path) != book["sha256"]:
        raise ValueError("O PDF mudou. Use a importação para substituir o livro.")
    with pymupdf.open(path) as document:
        if len(document) != book["page_count"]:
            raise ValueError("A quantidade de páginas difere do livro importado.")
        extracted = [extract_page_text(page) for page in document]
    if _file_hash(path) != book["sha256"]:
        raise ValueError("O PDF mudou durante a extração. Nenhum texto foi atualizado.")

    changed_pages = 0
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM books ORDER BY id LIMIT 1").fetchone()
        if current is None or tuple(current) != tuple(book):
            raise ValueError("O livro mudou durante a extração. Tente novamente.")
        pages = conn.execute(
            "SELECT id,pdf_page,text FROM pages WHERE book_id=? ORDER BY pdf_page",
            (book["id"],),
        ).fetchall()
        if [row["pdf_page"] for row in pages] != list(range(1, len(extracted) + 1)):
            raise ValueError("As páginas do índice estão incompletas. Reimporte o livro.")
        next_id = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM chunks").fetchone()[0]
        for page, text in zip(pages, extracted):
            if page["text"] == text:
                continue
            changed_pages += 1
            previous = conn.execute(
                "SELECT id FROM chunks WHERE page_id=? ORDER BY position", (page["id"],),
            ).fetchall()
            chunks = _chunks(text)
            conn.execute("UPDATE pages SET text=? WHERE id=?", (text, page["id"]))
            for position, chunk in enumerate(chunks):
                if position < len(previous):
                    chunk_id = previous[position]["id"]
                    conn.execute("UPDATE chunks SET text=? WHERE id=?", (chunk, chunk_id))
                    conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (chunk_id,))
                else:
                    chunk_id, next_id = next_id, next_id + 1
                    conn.execute(
                        "INSERT INTO chunks(id,page_id,position,text) VALUES (?,?,?,?)",
                        (chunk_id, page["id"], position, chunk),
                    )
                conn.execute("INSERT INTO chunks_fts(rowid,text) VALUES (?,?)", (chunk_id, chunk))
            for old in previous[len(chunks):]:
                conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (old["id"],))
                conn.execute("DELETE FROM chunks WHERE id=?", (old["id"],))
        if changed_pages:
            conn.execute("DELETE FROM vector_indices WHERE book_id=?", (book["id"],))
    return {"book": book["title"], "changed_pages": changed_pages,
            "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "rebuild_vectors": bool(changed_pages)}
