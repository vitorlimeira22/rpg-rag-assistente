import pytest

from rpg_assistente.book_tools import BookTools, SourceRegistry
from rpg_assistente.config import Settings
from rpg_assistente.db import connect


@pytest.fixture
def book(tmp_path):
    conn = connect(tmp_path / "book.sqlite3")
    with conn:
        book_id = conn.execute(
            "INSERT INTO books(title,edition,pdf_path,sha256,page_count) VALUES (?,?,?,?,?)",
            ("Livro de demonstração", "1.1", "private.pdf", "test-hash", 3),
        ).lastrowid
        for number, text in enumerate([
            "Definição do poder. Condição importante aparece no final da página.",
            "Outra opção possui alcance diferente e exige concentração.",
            "3",
        ], 1):
            page_id = conn.execute(
                "INSERT INTO pages(book_id,pdf_page,printed_page,chapter,section,text) "
                "VALUES (?,?,?,?,?,?)",
                (book_id, number, str(number + 10), "Capítulo", f"Seção {number}", text),
            ).lastrowid
            if number != 3:
                conn.execute("INSERT INTO chunks(page_id,position,text) VALUES (?,?,?)",
                             (page_id, 0, text[:20]))
    settings = Settings(tmp_path / "book.sqlite3", "", "", 0, 100, 10, None, None, False)
    try:
        yield conn, settings
    finally:
        conn.close()


def source(chunk_id=1, text="Uma fonte.", **extra):
    return {"id": chunk_id, "text": text, "title": "Livro", "edition": "1",
            "pdf_page": 1, "printed_page": "11", "chapter": "Capítulo",
            "section": "Seção", "methods": ["textual"], **extra}


def test_citations_are_stable_and_duplicate_sources_do_not_spend_budget():
    registry = SourceRegistry(max_sources=3, max_total_chars=50)
    first = registry.add(source())
    repeated = registry.add(source(text="Tentativa de trocar uma fonte já registrada."))
    page = registry.add(source(text="Página com contexto completo.", source_kind="page"))
    assert first["citation_id"] == repeated["citation_id"] == 1
    assert repeated["text"] == "Uma fonte."
    assert repeated["reused"] is True
    assert page["citation_id"] == 2
    assert registry.total_chars == len(first["text"]) + len(page["text"])
    assert len(registry.sources) == 2
    first["text"] = "alteração externa"
    assert registry.sources[0]["text"] == "Uma fonte."


def test_registry_bounds_source_count_and_total_text():
    registry = SourceRegistry(max_sources=2, max_total_chars=20)
    first = registry.add(source(text="Texto curto."))
    second = registry.add(source(2, "Uma segunda fonte longa demais."))
    assert first["truncated"] is False
    assert second["truncated"] is True
    assert registry.total_chars <= 20
    assert registry.add(source(3)) is None
    assert registry.warnings


def test_read_page_restores_tail_missing_from_chunk_and_keeps_image_id(book):
    conn, settings = book
    tools = BookTools(conn, settings)
    result = tools.read_book_pages([1, 2])
    assert result["status"] == "ok"
    assert len(result["sources"]) == 2
    first = result["sources"][0]
    assert first["source_kind"] == "page"
    assert first["id"] == 1
    assert first["page_id"] == 1
    assert first["pdf_page"] == 1 and first["printed_page"] == "11"
    assert "Condição importante aparece no final" in first["text"]
    assert first["truncated"] is False
    assert "pdf_path" not in str(result)
    assert "private.pdf" not in str(result)
    assert tools.read_book_pages([1])["sources"][0]["citation_id"] == 1


def test_page_and_registry_limits_signal_partial_evidence(book):
    conn, settings = book
    with conn:
        conn.execute("UPDATE pages SET text=? WHERE pdf_page=1", ("Regra longa. " * 1000,))
        conn.execute("UPDATE pages SET text=? WHERE pdf_page=2", ("Outra regra. " * 1000,))
    tools = BookTools(conn, settings, SourceRegistry(max_total_chars=7000))
    result = tools.read_book_pages([1, 2])
    assert all(item["truncated"] for item in result["sources"])
    assert all(item["returned_chars"] <= 6000 for item in result["sources"])
    assert sum(len(item["text"]) for item in result["sources"]) <= 7000
    assert all(item["original_chars"] == 13000 for item in result["sources"])
    assert result["warnings"]


def test_search_uses_canonical_sqlite_text_and_at_most_three_candidates(book):
    conn, settings = book
    captured = []
    def fake_retrieve(conn_arg, query, settings_arg, limit):
        captured.append((conn_arg, query, settings_arg, limit))
        return {"sources": [{"id": 1, "text": "Conteúdo não canônico", "pdf_path": "secret",
                             "methods": ["semântica"]}],
                "warnings": ["Busca sem reranking."], "retrieval": {"elapsed_ms": 1}}
    tools = BookTools(conn, settings, retriever=fake_retrieve)
    result = tools.search_book("Comparar opções")
    assert captured == [(conn, "Comparar opções", settings, 3)]
    assert result["sources"][0]["text"] == conn.execute(
        "SELECT text FROM chunks WHERE id=1"
    ).fetchone()[0]
    assert result["sources"][0]["methods"] == ["semântica"]
    assert "secret" not in str(result)
    assert result["warnings"] == ["Busca sem reranking."]


def test_search_returns_bounded_preview_preserving_full_page_for_reading(book):
    conn, settings = book
    full_text = "Fonte canônica com condições que precisam ser lidas. " * 40
    with conn:
        conn.execute("UPDATE chunks SET text=? WHERE id=1", (full_text,))
    tools = BookTools(conn, settings, retriever=lambda *args, **kwargs: {
        "sources": [{"id": 1}], "warnings": [],
    })
    result = tools.search_book("Comparar efeitos")
    preview = result["sources"][0]
    assert len(preview["text"]) <= 400
    assert full_text.startswith(preview["text"])
    assert preview["truncated"] is True
    assert preview["original_chars"] == len(full_text)
    assert preview["pdf_page"] == 1


@pytest.mark.parametrize("name,arguments", [
    ("search_book", {"query": "x"}),
    ("search_book", {"query": "   "}),
    ("search_book", {"query": "x" * 501}),
    ("search_book", {"query": 123}),
    ("search_book", {"query": "teste", "command": "ignored"}),
    ("read_book_pages", {"pdf_pages": [True]}),
    ("read_book_pages", {"pdf_pages": ["1"]}),
    ("read_book_pages", {"pdf_pages": [1.0]}),
    ("read_book_pages", {"pdf_pages": [0]}),
    ("read_book_pages", {"pdf_pages": [-1]}),
    ("read_book_pages", {"pdf_pages": [10001]}),
    ("read_book_pages", {"pdf_pages": [1, 2, 3]}),
    ("read_book_pages", {"pdf_pages": []}),
    ("read_book_pages", {"pdf_pages": [1], "path": "private.pdf"}),
    ("read_book_pages", '{"pdf_pages":[1]}'),
])
def test_invalid_arguments_are_rejected_before_retrieval(book, name, arguments):
    conn, settings = book
    def fail(*args, **kwargs):
        pytest.fail("Argumentos inválidos não devem executar recuperação.")
    tools = BookTools(conn, settings, retriever=fail)
    result = tools.execute(name, arguments)
    assert result["status"] == "invalid_arguments"
    assert result["sources"] == []


def test_unknown_tools_cannot_dispatch_arbitrary_methods(book):
    conn, settings = book
    tools = BookTools(conn, settings)
    assert tools.execute("__class__", {})["status"] == "unknown_tool"
    assert tools.execute("execute", {})["status"] == "unknown_tool"
    assert tools.execute(["read_book_pages"], {})["status"] == "unknown_tool"


def test_missing_or_nontext_pages_are_reported_without_invalid_sources(book):
    conn, settings = book
    tools = BookTools(conn, settings)
    result = tools.read_book_pages([3, 100])
    assert result["sources"] == []
    assert len(result["warnings"]) == 2
    duplicate = tools.read_book_pages([1, 1])
    assert len(duplicate["sources"]) == 1
    assert duplicate["requested_pdf_pages"] == [1]


def test_book_change_stops_tools_instead_of_mixing_editions(book):
    conn, settings = book
    tools = BookTools(conn, settings)
    with conn:
        conn.execute("UPDATE books SET sha256='another-file',edition='2'")
    assert tools.read_book_pages([1])["status"] == "book_changed"
    assert tools.search_book("alguma regra")["status"] == "book_changed"
    assert tools.registry.sources == []


def test_registry_can_be_seeded_with_initial_retrieval(book):
    conn, settings = book
    registry = SourceRegistry()
    registry.add(source())
    tools = BookTools(conn, settings, registry)
    page = tools.read_book_pages([1])["sources"][0]
    assert page["citation_id"] == 2
    assert len(registry.sources) == 2
