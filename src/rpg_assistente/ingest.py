from __future__ import annotations

import hashlib
from pathlib import Path
import re

import pymupdf

from .db import clear_books, connect
from .pdf_text import extract_page_text


def _chunks(text: str, max_chars: int = 1000, overlap: int = 120) -> list[str]:
    """Divide por página e palavras; preserva números quando acompanhados de texto.

    Não tenta reconhecer tabelas ou inventar texto de páginas sem OCR. A janela
    do encoder é tratada separadamente durante a indexação vetorial.
    """
    if max_chars < 1 or not 0 <= overlap < max_chars:
        raise ValueError("Use max_chars positivo e overlap menor que max_chars.")
    clean = re.sub(r"[ \t]+", " ", text).strip()
    if not any(char.isalpha() for char in clean):
        return []
    pieces: list[str] = []
    start = 0
    while start < len(clean):
        end = min(start + max_chars, len(clean))
        if end < len(clean):
            boundary = max(clean.rfind("\n", start + max_chars // 2, end),
                           clean.rfind(". ", start + max_chars // 2, end))
            if boundary > start:
                end = boundary + 1
            elif not clean[end].isspace() and not clean[end - 1].isspace():
                # Só uma palavra maior que a janela precisa ser fragmentada.
                word_end = max((match.start() for match in re.finditer(
                    r"\s+", clean[start:end]
                )), default=-1)
                if word_end > 0:
                    end = start + word_end
        piece = clean[start:end].strip()
        if any(char.isalpha() for char in piece):
            pieces.append(piece)
        if end >= len(clean):
            break
        next_start = max(start + 1, end - overlap)
        # Recuar dentro de uma palavra criava falsos termos como "oque".
        while next_start < end and not clean[next_start - 1].isspace():
            next_start += 1
        start = next_start
    return pieces


def _bookmarks(doc: pymupdf.Document) -> dict[int, tuple[str | None, str | None]]:
    chapter = None
    section = None
    entries: dict[int, list[tuple[int, str]]] = {}
    for level, title, page in doc.get_toc():
        if page >= 1:
            entries.setdefault(page, []).append((level, title.strip()))
    result: dict[int, tuple[str | None, str | None]] = {}
    for page in range(1, len(doc) + 1):
        for level, title in entries.get(page, []):
            if level == 1:
                chapter, section = title, None
            elif level == 2:
                section = title
            elif level == 3 and len(entries[page]) == 1:
                section = title
        result[page] = (chapter, section)
    return result


def import_pdf(pdf_path: Path, db_path: Path, title: str, edition: str,
               vector_path: Path | None = None) -> dict:
    pdf_path = pdf_path.expanduser().resolve(strict=True)
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("O arquivo precisa ser um PDF.")
    digest = hashlib.sha256()
    with pdf_path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)

    with pymupdf.open(pdf_path) as doc:
        if len(doc) == 0:
            raise ValueError("O PDF está vazio.")
        bookmarks = _bookmarks(doc)
        conn = connect(db_path)
        empty_pages: list[int] = []
        chunk_count = 0
        old_collections: list[str] = []
        try:
            old_collections = [row[0] for row in conn.execute(
                "SELECT collection FROM vector_indices"
            )]
            with conn:
                clear_books(conn)
                book_id = conn.execute(
                    "INSERT INTO books(title, edition, pdf_path, sha256, page_count) VALUES (?, ?, ?, ?, ?)",
                    (title, edition, str(pdf_path), digest.hexdigest(), len(doc)),
                ).lastrowid
                for index, page in enumerate(doc):
                    page_number = index + 1
                    # A ordem original mantém juntas as colunas deste livro.
                    # A inspeção visual continua necessária para tabelas e caixas laterais.
                    extracted = extract_page_text(page)
                    if len(extracted.strip()) < 30:
                        empty_pages.append(page_number)
                    chapter, section = bookmarks[page_number]
                    label = page.get_label() or None
                    page_id = conn.execute(
                        "INSERT INTO pages(book_id, pdf_page, printed_page, chapter, section, text) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (book_id, page_number, label, chapter, section, extracted),
                    ).lastrowid
                    for position, chunk in enumerate(_chunks(extracted)):
                        chunk_id = conn.execute(
                            "INSERT INTO chunks(page_id, position, text) VALUES (?, ?, ?)",
                            (page_id, position, chunk),
                        ).lastrowid
                        conn.execute(
                            "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
                            (chunk_id, chunk),
                        )
                        chunk_count += 1
        finally:
            conn.close()
        if vector_path is not None and vector_path.is_dir() and old_collections:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            client = chromadb.PersistentClient(
                path=str(vector_path), settings=ChromaSettings(anonymized_telemetry=False)
            )
            existing = {item.name for item in client.list_collections()}
            for collection in old_collections:
                if collection in existing:
                    client.delete_collection(collection)
        return {
            "title": title,
            "edition": edition,
            "pdf_pages": len(doc),
            "chunks": chunk_count,
            "pages_with_little_text": empty_pages,
            "sha256": digest.hexdigest(),
        }
