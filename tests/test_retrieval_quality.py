from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from rpg_assistente.config import Settings
from rpg_assistente.db import connect
from rpg_assistente.ingest import _chunks, import_pdf
from rpg_assistente.search import _terms, retrieve
from rpg_assistente.vector import LocalEmbeddings, build_vector_index
from test_pipeline import make_pdf


def config(tmp_path: Path) -> Settings:
    return Settings(tmp_path / "book.sqlite3", "", "", 0, 100, 10, None, None,
                    False, tmp_path / "chroma", "fake")


def import_texts(tmp_path: Path, texts: list[str]) -> Settings:
    pdf = tmp_path / "book.pdf"
    with pymupdf.open() as doc:
        for text in texts:
            doc.new_page().insert_text((50, 50), text)
        doc.save(pdf)
    settings = config(tmp_path)
    import_pdf(pdf, settings.db_path, "Livro sintético", "1")
    return settings


def test_chunks_keep_word_boundaries_and_legitimate_short_rules():
    text = " ".join(f"palavra{number}" for number in range(80))
    chunks = _chunks(text, max_chars=105, overlap=20)
    original_words = set(text.split())
    assert set(word for chunk in chunks for word in chunk.split()) == original_words
    assert all(len(chunk) <= 105 for chunk in chunks)
    assert _chunks("112\n307 1 2 3 4") == []
    assert _chunks("PV: 8") == ["PV: 8"]
    with pytest.raises(ValueError):
        _chunks("abc", max_chars=10, overlap=10)


def test_numeric_pages_are_preserved_but_not_indexed(tmp_path):
    settings = import_texts(tmp_path, ["112", "PV: 8"])
    conn = connect(settings.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 2
        chunks = conn.execute("SELECT text FROM chunks").fetchall()
        assert [row["text"] for row in chunks] == ["PV: 8"]
    finally:
        conn.close()


def test_short_rule_acronyms_survive_normalization():
    assert _terms("Quanto PE posso gastar?") == ["pe", "gastar"]
    assert _terms("PV, DT e NEX") == ["pv", "dt", "nex"]
    assert _terms("aos 5 de 2026") == []


def test_partial_matches_complement_exact_matches(tmp_path):
    settings = import_texts(tmp_path, ["Teste de pericia.", "Pericia exige treinamento."])
    conn = connect(settings.db_path)
    try:
        result = retrieve(conn, "teste pericia", settings)
        assert [row["pdf_page"] for row in result["sources"]] == [1, 2]
        assert result["retrieval"]["lexical_candidates"] == 2
        assert result["warnings"]  # Ausência de índice vetorial explicitada.
    finally:
        conn.close()


def test_vector_failure_preserves_lexical_sources_and_redacts_details(tmp_path, monkeypatch):
    import rpg_assistente.vector as vector
    settings = import_texts(tmp_path, ["Teste de pericia."])
    conn = connect(settings.db_path)

    def fail(*args, **kwargs):
        raise RuntimeError("private-path-and-sensitive-detail")

    monkeypatch.setattr(vector, "vector_search", fail)
    try:
        result = retrieve(conn, "teste", settings)
        assert result["sources"][0]["pdf_page"] == 1
        assert "Busca vetorial indisponível" in result["warnings"][0]
        assert "sensitive" not in str(result)
    finally:
        conn.close()


def test_two_complementary_chunks_on_same_page_can_reach_context(tmp_path):
    text = ("Energia permite realizar poderes. " * 20
            + "Energia retorna quando o personagem descansa. " * 20)
    settings = import_texts(tmp_path, [text])  # insert_text pode ultrapassar largura; texto continua selecionável.
    # Inserir chunks curtos controla o caso sem depender do layout do PDF.
    conn = connect(settings.db_path)
    try:
        page_id = conn.execute("SELECT id FROM pages").fetchone()[0]
        conn.execute("DELETE FROM chunks_fts")
        conn.execute("DELETE FROM chunks")
        for pos, sentence in enumerate([
            "Energia permite realizar poderes durante uma cena.",
            "Energia retorna após um descanso em lugar seguro.",
        ]):
            chunk_id = conn.execute("INSERT INTO chunks(page_id,position,text) VALUES (?,?,?)",
                                    (page_id, pos, sentence)).lastrowid
            conn.execute("INSERT INTO chunks_fts(rowid,text) VALUES (?,?)", (chunk_id, sentence))
        conn.commit()
        result = retrieve(conn, "energia", settings)
        assert len(result["sources"]) == 2
        assert {row["pdf_page"] for row in result["sources"]} == {1}
    finally:
        conn.close()


def test_embedding_windows_cover_tail_without_exceeding_encoder_budget():
    words = [f"item{n}" for n in range(40)]
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, **{w: n + 1 for n, w in enumerate(words)}},
                                          unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    encoder = LocalEmbeddings.__new__(LocalEmbeddings)
    encoder._tokenizer = tokenizer
    encoder._token_budget = 8
    windows = encoder.passages(" ".join(words))
    assert len(windows) > 1
    assert set(word for window in windows for word in window.split()) == set(words)
    assert all(len(tokenizer.encode(window, add_special_tokens=False).ids) <= 8 for window in windows)


def test_reranker_reorders_pairs_and_preserves_source_identity(tmp_path, monkeypatch):
    import rpg_assistente.rerank as rerank
    class FakeCrossEncoder:
        def rerank(self, query, passages, batch_size):
            assert query == "energia"
            assert len(passages) == 2
            return [0.1, 0.9]
    monkeypatch.setattr(rerank, "_encoder", lambda *args: FakeCrossEncoder())
    rows = [{"id": 1, "text": "A energia existe.", "methods": ["textual"]},
            {"id": 2, "text": "Recupere energia ao descansar.", "methods": ["semântica"]}]
    result = rerank.rerank_candidates("energia", rows, config(tmp_path))
    assert [row["id"] for row in result] == [2, 1]
    assert result[0]["methods"] == ["semântica", "reranking"]
    assert rows[1]["methods"] == ["semântica"]  # A entrada não é alterada.


def test_reranker_failure_is_visible_and_keeps_hybrid_results(tmp_path, monkeypatch):
    import rpg_assistente.rerank as rerank
    settings = replace(import_texts(tmp_path, ["Energia recupera poderes."]), rerank_enabled=True)
    monkeypatch.setattr(rerank, "rerank_candidates",
                        lambda *args: (_ for _ in ()).throw(rerank.RerankUnavailable()))
    conn = connect(settings.db_path)
    try:
        result = retrieve(conn, "energia", settings)
        assert result["sources"]
        assert result["retrieval"]["rerank_status"] == "unavailable"
        assert any("Reranking indisponível" in warning for warning in result["warnings"])
    finally:
        conn.close()


def test_page_diversification_can_fill_from_candidates_after_rerank_cutoff(tmp_path, monkeypatch):
    import rpg_assistente.rerank as rerank
    import rpg_assistente.search as search_module
    import rpg_assistente.vector as vector

    settings = replace(config(tmp_path), rerank_enabled=True, rerank_candidates=5)
    rows = [
        {"id": number, "text": f"Energia fonte{number}.", "title": "Livro",
         "edition": "1", "pdf_page": 1 if number <= 5 else number}
        for number in range(1, 9)
    ]
    monkeypatch.setattr(search_module, "search", lambda *args, **kwargs: rows)
    monkeypatch.setattr(vector, "vector_search", lambda *args, **kwargs: [])

    def fake_rerank(query, candidates, config):
        assert len(candidates) == 5
        return [{**row, "methods": [*row["methods"], "reranking"]}
                for row in reversed(candidates)]

    monkeypatch.setattr(rerank, "rerank_candidates", fake_rerank)
    conn = connect(settings.db_path)
    try:
        result = retrieve(conn, "energia", settings, limit=5)
        assert [row["id"] for row in result["sources"]] == [5, 4, 6, 7, 8]
        assert "reranking" in result["sources"][0]["methods"]
        assert "reranking" not in result["sources"][-1]["methods"]
    finally:
        conn.close()


def test_failed_reindex_keeps_previous_collection_usable(tmp_path, monkeypatch):
    import rpg_assistente.vector as vector
    from langchain_core.embeddings import Embeddings
    class FakeEmbeddings(Embeddings):
        def embed_documents(self, texts):
            return [[1.0, 0.0] for text in texts]
        def embed_query(self, text):
            return [1.0, 0.0]
        def passages(self, text):
            return text.split("\n", maxsplit=1)
    monkeypatch.setattr(vector, "_embeddings", lambda *args: FakeEmbeddings())
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    settings = config(tmp_path)
    import_pdf(pdf, settings.db_path, "Livro de teste", "1")
    conn = connect(settings.db_path)
    try:
        first = build_vector_index(conn, settings)
        assert first["vectors"] == 4
        assert first["chunks"] == 2
        with monkeypatch.context() as patch:
            def fail(*args, **kwargs):
                raise RuntimeError("Falha de escrita simulada")
            patch.setattr(vector.Chroma, "add_documents", fail)
            with pytest.raises(RuntimeError):
                build_vector_index(conn, settings)
        row = conn.execute("SELECT collection FROM vector_indices").fetchone()
        assert row["collection"] == first["collection"]
        found = vector.vector_search(conn, "energia", settings)
        assert len(found) == 2  # Quatro janelas representam duas fontes canônicas.
        assert len({row["id"] for row in found}) == 2
        assert all("\n" in row["text"] for row in found)
        assert [collection.name for collection in vector._client(settings).list_collections()] == [
            first["collection"]
        ]
    finally:
        conn.close()
