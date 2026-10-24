from pathlib import Path

import pymupdf

import rpg_assistente.config as config_module
from rpg_assistente.config import Settings
from rpg_assistente.db import connect
from rpg_assistente.ingest import import_pdf
from rpg_assistente.search import search, source
from rpg_assistente.search import hybrid_search
from rpg_assistente.vector import build_vector_index


def make_pdf(path: Path) -> None:
    with pymupdf.open() as doc:
        first = doc.new_page()
        first.insert_text((70, 70), "Capitulo 1: Testes", fontsize=14)
        first.insert_text((70, 100), "Um teste de pericia usa dados e um bonus de treinamento.")
        second = doc.new_page()
        second.insert_text((70, 70), "Capitulo 2: Energia", fontsize=14)
        second.insert_text((70, 100), "Pontos de esforco representam energia do personagem.")
        doc.set_toc([[1, "Testes", 1], [1, "Energia", 2]])
        doc.save(path)


def settings(db_path: Path) -> Settings:
    return Settings(db_path, "test-key", "test-model", 2, 100, 10, None, None, True)


def test_import_search_and_source_keep_page_reference(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    db_path = tmp_path / "book.sqlite3"
    report = import_pdf(pdf, db_path, "Livro próprio", "1")
    assert report["pdf_pages"] == 2
    assert report["chunks"] == 2
    conn = connect(db_path)
    try:
        results = search(conn, "teste de perícia")
        assert len(results) == 1
        assert results[0]["pdf_page"] == 1
        assert source(conn, results[0]["id"])["pdf_path"] == str(pdf)
        assert search(conn, "espada laser") == []
    finally:
        conn.close()


def test_reimport_replaces_index_without_duplicates(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    db_path = tmp_path / "book.sqlite3"
    import_pdf(pdf, db_path, "Livro próprio", "1")
    import_pdf(pdf, db_path, "Outro livro", "1")
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
        assert conn.execute("SELECT title FROM books").fetchone()[0] == "Outro livro"
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 2
    finally:
        conn.close()


def test_project_env_does_not_use_ambient_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "ambient-key")
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=\n", encoding="utf-8")
    assert config_module.get_settings().groq_api_key == ""
    env_file.write_text("GROQ_API_KEY=local-key\n", encoding="utf-8")
    assert config_module.get_settings().groq_api_key == "local-key"


def test_hybrid_retrieval_finds_paraphrase_and_keeps_source(tmp_path: Path, monkeypatch) -> None:
    """Exercita Chroma/LangChain com vetores previsíveis, sem baixar modelo no teste."""
    from langchain_core.embeddings import Embeddings
    import rpg_assistente.vector as vector_module

    class FakeEmbeddings(Embeddings):
        def __init__(self, model: str, cache_dir: str, local_files_only: bool = False):
            pass

        def encode(self, text: str) -> list[float]:
            text = text.lower()
            if any(word in text for word in ("energia", "esforco", "vigor")):
                return [1.0, 0.0]
            if "laser" in text:
                return [0.0, 1.0]
            return [0.0, 1.0]

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [self.encode(text) for text in texts]

        def embed_query(self, text: str) -> list[float]:
            return self.encode(text)

    monkeypatch.setattr(vector_module, "LocalEmbeddings", FakeEmbeddings)
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    db_path = tmp_path / "book.sqlite3"
    import_pdf(pdf, db_path, "Livro próprio", "1")
    config = Settings(db_path, "", "", 0, 100, 10, None, None, False,
                      tmp_path / "chroma", "fake")
    conn = connect(db_path)
    try:
        report = build_vector_index(conn, config)
        assert report["vectors"] == 2
        assert search(conn, "Como recuperar vigor?") == []
        found = hybrid_search(conn, "Como recuperar vigor?", config)
        assert found[0]["pdf_page"] == 2
        assert found[0]["methods"] == ["semântica"]
    finally:
        conn.close()
    import_pdf(pdf, db_path, "Outro livro", "2", config.vector_path)
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM vector_indices").fetchone()[0] == 0
        assert hybrid_search(conn, "Como recuperar vigor?", config) == []
    finally:
        conn.close()


def test_hybrid_recovers_when_question_has_extra_words(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    db_path = tmp_path / "book.sqlite3"
    import_pdf(pdf, db_path, "Livro próprio", "1")
    conn = connect(db_path)
    try:
        question = "Como executar um teste de pericia dificil?"
        assert search(conn, question) == []
        found = hybrid_search(conn, question, settings(db_path))
        assert found[0]["pdf_page"] == 1
    finally:
        conn.close()


def test_semantic_candidate_is_not_rejected_by_unvalidated_cutoff(tmp_path: Path, monkeypatch) -> None:
    import rpg_assistente.vector as vector_module
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    db_path = tmp_path / "book.sqlite3"
    import_pdf(pdf, db_path, "Livro próprio", "1")
    conn = connect(db_path)
    try:
        row = source(conn, 2)
        row.pop("pdf_path")
        row["distance"] = 0.48
        monkeypatch.setattr(vector_module, "vector_search", lambda *args, **kwargs: [row])
        assert hybrid_search(conn, "vigor", settings(db_path))[0]["id"] == 2
    finally:
        conn.close()
