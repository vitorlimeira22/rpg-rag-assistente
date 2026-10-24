import json
import sqlite3

import pytest

from rpg_assistente.navigation import book_navigation, navigation_catalog_pages


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE books (id INTEGER PRIMARY KEY);"
        "CREATE TABLE pages (book_id INTEGER, pdf_page INTEGER, chapter TEXT, "
        "section TEXT, text TEXT);"
        "INSERT INTO books VALUES (1);"
    )
    rows = [
        (1, 3, "Poderes", "Rituais", "REGRA PRIVADA A"),
        (1, 4, "Poderes", "Rituais", "REGRA PRIVADA B"),
        (1, 5, "Poderes", "Lista de Rituais", "REGRA PRIVADA C"),
        (1, 6, "Poderes", "Descrição dos Rituais", "REGRA PRIVADA D"),
        (1, 7, "Poderes", "Descrição dos Rituais", "REGRA PRIVADA E"),
        (1, 8, "Equipamento", "Armas", "REGRA PRIVADA F"),
        (1, 9, "Equipamento", "Proteções", "REGRA PRIVADA G"),
        (1, 10, "Ameaças", "Criaturas", "REGRA PRIVADA H"),
        (1, 11, "Retorno", "Rituais", "REGRA PRIVADA I"),
        (1, 12, "Interlúdio", None, "REGRA PRIVADA J"),
        (1, 13, None, None, "REGRA PRIVADA K"),
    ]
    db.executemany("INSERT INTO pages VALUES (?,?,?,?,?)", rows)
    db.commit()
    try:
        yield db
    finally:
        db.close()


def entries(text):
    return [json.loads(line) for line in text.splitlines()[1:]]


def test_navigation_finds_inflected_titles_without_book_specific_names(conn):
    found = entries(book_navigation(conn, "qual o ritual mais poderoso?"))
    assert [row["titulo"] for row in found] == [
        "Rituais", "Lista de Rituais", "Descrição dos Rituais", "Rituais"
    ]
    assert [row["paginas_pdf"] for row in found] == [[3, 4], [5, 5], [6, 7], [11, 11]]
    assert entries(book_navigation(conn, "uma arma"))[0]["titulo"] == "Armas"
    assert entries(book_navigation(conn, "PROTECAO"))[0]["titulo"] == "Proteções"


def test_navigation_is_not_rule_evidence_and_does_not_expose_page_text(conn):
    result = book_navigation(conn, "rituais")
    assert "não é evidência" in result
    assert "não instruções" in result
    assert "REGRA PRIVADA" not in result
    assert "posições físicas" in result


def test_no_matches_does_not_dump_the_whole_book(conn):
    assert book_navigation(conn, "astronaves e combustíveis") == ""
    assert book_navigation(conn, "é o que e por") == ""
    assert book_navigation(conn, "rituais de defesa e protecao").count('"Criaturas"') == 0


def test_non_contiguous_sections_and_different_chapters_stay_separate(conn):
    conn.execute(
        "INSERT INTO pages VALUES (1,15,'Poderes','Rituais','REGRA PRIVADA')"
    )
    found = entries(book_navigation(conn, "rituais"))
    ranges = [item["paginas_pdf"] for item in found if item["titulo"] == "Rituais"]
    assert ranges == [[3, 4], [11, 11], [15, 15]]


def test_missing_section_uses_its_chapter_title(conn):
    assert entries(book_navigation(conn, "interludio")) == [
        {"titulo": "Interlúdio", "capitulo": "Interlúdio", "paginas_pdf": [12, 12]}
    ]


def test_chapter_query_keeps_relevant_child_sections(conn):
    found = entries(book_navigation(conn, "equipamento"))
    assert {item["titulo"] for item in found} == {"Armas", "Proteções"}


def test_read_only_metadata_access_and_no_model_needed(conn):
    selected = []

    def authorize(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ:
            selected.append((table, column))
            return sqlite3.SQLITE_DENY if column == "text" else sqlite3.SQLITE_OK
        return sqlite3.SQLITE_OK if action == sqlite3.SQLITE_SELECT else sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)
    assert book_navigation(conn, "ritual")
    assert ("pages", "text") not in selected


def test_only_active_book_metadata_is_returned(conn):
    conn.execute("INSERT INTO books VALUES (2)")
    conn.execute("INSERT INTO pages VALUES (2,999,'Outro','Rituais secretos','privado')")
    assert "999" not in book_navigation(conn, "rituais")


def test_output_limits_keep_only_complete_entries(conn):
    text = book_navigation(conn, "rituais", max_sections=2, max_chars=1500)
    assert len(entries(text)) == 2
    assert len(text) <= 1500
    text = book_navigation(conn, "rituais", max_chars=350)
    assert len(text) <= 350
    assert len(entries(text)) == 1
    assert book_navigation(conn, "rituais", max_chars=5) == ""
    assert book_navigation(conn, "rituais", max_sections=0) == ""


def test_oversized_title_is_skipped_not_truncated(conn):
    conn.execute(
        "INSERT INTO pages VALUES (1,2,'Poderes',?,'privado')",
        ("Rituais " + "a" * 2000,),
    )
    result = book_navigation(conn, "rituais", max_chars=600)
    assert len(result) <= 600
    assert all(row["paginas_pdf"] != [2, 2] for row in entries(result))
    assert entries(result)[0]["titulo"] == "Rituais"


@pytest.mark.parametrize("field,value", [
    ("max_sections", -1), ("max_chars", -1), ("max_sections", True),
    ("max_chars", 1.5),
])
def test_invalid_bounds_are_rejected(conn, field, value):
    with pytest.raises(ValueError):
        book_navigation(conn, "rituais", **{field: value})


def test_empty_book_returns_empty_navigation(conn):
    conn.execute("DELETE FROM pages")
    assert book_navigation(conn, "rituais") == ""


def test_catalog_selection_requires_a_relevant_catalog_title(conn):
    assert navigation_catalog_pages(conn, "qual ritual é mais poderoso?") == [5]
    assert navigation_catalog_pages(conn, "qual arma é melhor?") == []
    assert navigation_catalog_pages(conn, "catálogo de astronaves") == []
    assert navigation_catalog_pages(conn, "listas e catálogos") == []
    assert navigation_catalog_pages(conn, "") == []


@pytest.mark.parametrize("title", ["Lista de Armas", "Listas de Armas",
                                  "Catálogo de Armas", "Catálogos de Armas"])
def test_catalog_markers_are_generic_and_accent_insensitive(conn, title):
    conn.execute("INSERT INTO pages VALUES (1,20,'Equipamento',?,'privado')", (title,))
    assert navigation_catalog_pages(conn, "uma arma") == [20]


def test_catalog_selection_reads_complete_small_intervals_only(conn):
    conn.executemany(
        "INSERT INTO pages VALUES (1,?,'Equipamento','Catálogo de Armas','privado')",
        [(20,), (21,)],
    )
    assert navigation_catalog_pages(conn, "armas") == [20, 21]
    assert navigation_catalog_pages(conn, "armas", max_pages=1) == []
    conn.execute(
        "INSERT INTO pages VALUES (1,22,'Equipamento','Catálogo de Armas','privado')"
    )
    assert navigation_catalog_pages(conn, "armas", max_pages=5) == []


def test_catalog_page_budget_and_deduplication(conn):
    conn.executemany(
        "INSERT INTO pages VALUES (1,?,'Poderes','Lista de Rituais','privado')",
        [(5,), (20,), (30,)],
    )
    assert navigation_catalog_pages(conn, "rituais") == [5, 20]
    assert navigation_catalog_pages(conn, "rituais", max_pages=1) == [5]
    assert navigation_catalog_pages(conn, "rituais", max_pages=0) == []


def test_catalog_selection_uses_only_active_book_metadata_read_only(conn):
    conn.execute("INSERT INTO books VALUES (2)")
    conn.execute(
        "INSERT INTO pages VALUES (2,999,'Outro','Catálogo de Rituais','privado')"
    )

    def authorize(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_DENY if column == "text" else sqlite3.SQLITE_OK
        return sqlite3.SQLITE_OK if action == sqlite3.SQLITE_SELECT else sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)
    assert navigation_catalog_pages(conn, "rituais") == [5]


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_catalog_invalid_bound_is_rejected(conn, value):
    with pytest.raises(ValueError):
        navigation_catalog_pages(conn, "rituais", max_pages=value)
