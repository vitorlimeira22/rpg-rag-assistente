from pathlib import Path

import pymupdf
import pytest

from rpg_assistente.db import connect
from rpg_assistente.ingest import import_pdf
from rpg_assistente import maintenance
from rpg_assistente.conversations import ConversationInput, create_conversation


def setup_book(tmp_path: Path):
    pdf = tmp_path / "book.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((70, 70), "Rolagem antiga")
        doc.new_page().insert_text((70, 70), "Outra pagina preservada")
        doc.save(pdf)
    db = tmp_path / "test.sqlite3"
    import_pdf(pdf, db, "Livro de teste", "1")
    conn = connect(db)
    with conn:
        conn.execute("INSERT INTO vector_indices(book_id,model,collection,chunk_count) VALUES(1,'fake','old',2)")
        conn.execute("INSERT INTO characters(name,character_class,data_json) VALUES('Ana','combatente','{}')")
        conn.execute("INSERT INTO campaigns(name) VALUES('Mesa')")
    create_conversation(conn, ConversationInput(title="Conversa preservada"))
    return conn, pdf


def test_refresh_updates_text_and_fts_and_invalidates_vectors_without_losing_user_data(tmp_path, monkeypatch):
    conn, _ = setup_book(tmp_path)
    try:
        before = {table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]
                  for table in ("books", "characters", "campaigns", "conversations")}
        source_pages = [tuple(r) for r in conn.execute("SELECT id,page_id FROM chunks ORDER BY id")]
        monkeypatch.setattr(maintenance, "extract_page_text",
                            lambda p: p.get_text().replace("Rolagem antiga", "Rolagem 2d20"))
        result = maintenance.refresh_book_text(conn)
        assert result["changed_pages"] == 1 and result["rebuild_vectors"] is True
        assert conn.execute("SELECT COUNT(*) FROM vector_indices").fetchone()[0] == 0
        assert [tuple(r) for r in conn.execute("SELECT id,page_id FROM chunks ORDER BY id")] == source_pages
        assert conn.execute("SELECT text FROM pages WHERE pdf_page=1").fetchone()[0] == "Rolagem 2d20\n"
        assert conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '2d20'").fetchone()[0] == 1
        assert conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'antiga'").fetchone() is None
        for table, rows in before.items():
            assert [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")] == rows
        assert maintenance.refresh_book_text(conn)["changed_pages"] == 0
    finally:
        conn.close()


def test_refresh_no_changes_keeps_vector_index(tmp_path):
    conn, _ = setup_book(tmp_path)
    try:
        assert maintenance.refresh_book_text(conn)["rebuild_vectors"] is False
        assert conn.execute("SELECT collection FROM vector_indices").fetchone()[0] == "old"
    finally:
        conn.close()


def test_refresh_rejects_changed_pdf_without_writes(tmp_path):
    conn, pdf = setup_book(tmp_path)
    try:
        with pdf.open("ab") as stream:
            stream.write(b"\n% changed\n")
        with pytest.raises(ValueError, match="PDF mudou"):
            maintenance.refresh_book_text(conn)
        assert conn.execute("SELECT text FROM chunks WHERE id=1").fetchone()[0] == "Rolagem antiga"
        assert conn.execute("SELECT collection FROM vector_indices").fetchone()[0] == "old"
    finally:
        conn.close()


def test_refresh_does_not_partially_write_on_extraction_failure(tmp_path, monkeypatch):
    conn, _ = setup_book(tmp_path)
    def fail_on_second(page):
        if page.number == 1:
            raise ValueError("Falha na extração")
        return "Novo texto"
    monkeypatch.setattr(maintenance, "extract_page_text", fail_on_second)
    try:
        with pytest.raises(ValueError, match="Falha na extração"):
            maintenance.refresh_book_text(conn)
        assert conn.execute("SELECT text FROM chunks WHERE id=1").fetchone()[0] == "Rolagem antiga"
        assert conn.execute("SELECT collection FROM vector_indices").fetchone()[0] == "old"
    finally:
        conn.close()
