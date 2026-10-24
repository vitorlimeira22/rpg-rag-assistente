"""Fichas fictícias exercitam estrutura e invariantes, sem reproduzir o livro."""

import sqlite3

import pytest

from rpg_assistente.book_tools import SourceRegistry
from rpg_assistente.creature_catalog import (
    build_creature_catalog, creature_catalog_intent, render_creature_selection, select_creatures,
)
from rpg_assistente.db import connect


STATS = "\nPRESENÇA PERTURBADORA\nDT 10\nPONTOS DE VIDA\n20\nDEFESA\n15\n"


@pytest.fixture
def catalog_book(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    conn = connect(path)
    with conn:
        conn.execute(
            "INSERT INTO books(title,edition,pdf_path,sha256,page_count) VALUES (?,?,?,?,?)",
            ("Ordem Paranormal RPG", "1.1", "not-exposed.pdf", "synthetic-hash", 0),
        )

    def append(text, section="Seção", chunks=True):
        with conn:
            number = conn.execute("SELECT count(*) FROM pages").fetchone()[0] + 1
            page_id = conn.execute(
                "INSERT INTO pages(book_id,pdf_page,printed_page,chapter,section,text) "
                "VALUES (1,?,?,?,?,?)", (number, str(number + 7), "Ameaças", section, text),
            ).lastrowid
            if chunks:
                conn.execute("INSERT INTO chunks(page_id,position,text) VALUES (?,0,?)",
                             (page_id, text[:40]))
            conn.execute("UPDATE books SET page_count=?", (number,))
        return number

    yield conn, append, path
    conn.close()


def test_primary_element_comes_from_header_not_secondary_descriptors(catalog_book):
    conn, append, _ = catalog_book
    append("GUARDIÃO\nSANGUE\nCONHECIMENTO  MEDO  CRIATURA\nVD0400" + STATS, "Guardião")
    append("SENTINELA\nCONHECIMENTO\nSANGUE  CRIATURA\nVD40" + STATS, "Sentinela")
    result = select_creatures(conn)
    entries = result["structured_catalog"]["entries"]
    assert [(e["name"], e["primary_element"], e["vd"]) for e in entries] == [
        ("GUARDIÃO", "Sangue", 400), ("SENTINELA", "Conhecimento", 40),
    ]
    assert result["structured_catalog"]["coverage"]["max_vd_by_primary_element"] == {
        "Sangue": 400, "Conhecimento": 40,
    }
    assert "Medo" not in result["structured_catalog"]["coverage"]["counts_by_primary_element"]
    assert "not-exposed" not in str(result)


def test_presentation_preserves_ties_sources_definition_and_partial_coverage(catalog_book):
    conn, append, _ = catalog_book
    append("ALFA\nSANGUE\nVD80" + STATS, "Alfa")
    append("BETA\nSANGUE\nVD80" + STATS, "Beta")
    append("Não foi possível extrair o cabeçalho.\nVD200" + STATS, "Incompleta")
    append("Nome e VD\nNome e valor de desafio (VD) da ameaça.\n", "Fichas")
    selection = select_creatures(conn)
    text = render_creature_selection(selection)
    assert "| Sangue | ALFA | 80 | [1] |" in text
    assert "| Sangue | BETA | 80 | [2] |" in text
    assert "valor de desafio** [3]" in text
    assert "extração está incompleta" in text
    assert "4 páginas do PDF examinadas; 2 fichas" in text
    assert "empates" in text


def test_multiline_name_element_word_and_vd_extracted_at_page_end(catalog_book):
    conn, append, _ = catalog_book
    append("DAMA DE\nSANGUE\nSANGUE\nCRIATURA\n" + STATS + "VD60", "Dama de Sangue")
    append(STATS + "Texto de habilidade.\nVIGIA\nVD080\nENERGIA", "Vigia")
    entries = build_creature_catalog(conn)["entries"]
    assert [(e["name"], e["primary_element"], e["vd"]) for e in entries] == [
        ("DAMA DE SANGUE", "Sangue", 60), ("VIGIA", "Energia", 80),
    ]


def test_missing_stylized_element_requires_explicit_text_not_enigma(catalog_book):
    conn, append, _ = catalog_book
    append("VD320\nSEM ROSTO\nENIGMA DE MEDO\n" + STATS +
           "CRIATURA DO MEDO\nTexto de habilidade.", "Sem Rosto")
    append("VD200\nSEM COR\nENIGMA DE MEDO\n" + STATS, "Sem Cor")
    result = build_creature_catalog(conn)
    assert len(result["entries"]) == 1
    assert result["entries"][0]["primary_element"] == "Medo"
    assert result["entries"][0]["classification_basis"] == "explicit_creature_label"
    assert result["coverage"]["unparsed_pages"] == [2]
    assert not result["coverage"]["complete_for_detected_statblocks"]


def test_unparsed_candidate_and_missing_chunk_prevent_exhaustive_claim(catalog_book):
    conn, append, _ = catalog_book
    append("ALFA\nSANGUE\nVD80" + STATS, "Alfa")
    append("Texto sem nome extraível.\nVD400" + STATS, "Incompleta")
    append("BETA\nMORTE\nVD500" + STATS, "Beta", chunks=False)
    result = select_creatures(conn)
    coverage = result["structured_catalog"]["coverage"]
    assert coverage["candidate_statblocks"] == 3
    assert coverage["parsed_statblocks"] == 1
    assert coverage["unparsed_pages"] == [2, 3]
    assert coverage["complete_for_detected_statblocks"] is False
    assert any("Cobertura incompleta" in warning for warning in result["warnings"])


def test_prose_mentions_and_mundane_threats_are_outside_declared_scope(catalog_book):
    conn, append, _ = catalog_book
    append("Um exemplo com VD400 não é uma ficha.")
    append("GUARDA\nVD20\nDEFESA\n15\nPONTOS DE VIDA\n20", "Guarda")
    append("ECO\nMORTE\nVD40" + STATS, "Eco")
    result = build_creature_catalog(conn)
    assert result["coverage"]["scanned_pages"] == 3
    assert result["coverage"]["candidate_statblocks"] == 1
    assert result["coverage"]["complete_for_detected_statblocks"]
    assert "mundanas" in result["scope"]


def test_multiple_vds_or_conflicting_headers_are_not_silently_selected(catalog_book):
    conn, append, _ = catalog_book
    append("ALFA\nSANGUE\nVD20\nBETA\nSANGUE\nVD40" + STATS)
    append("GAMA\nSANGUE\nTexto.\nDELTA\nMORTE\nVD60" + STATS)
    result = build_creature_catalog(conn)
    assert result["coverage"]["candidate_statblocks"] == 3
    assert result["coverage"]["unparsed_pages"] == [1, 2]
    assert result["entries"] == []


def test_missing_vd_on_statblock_is_a_coverage_failure(catalog_book):
    conn, append, _ = catalog_book
    append("ALFA\nSANGUE\nVD40" + STATS, "Alfa")
    append("BETA\nMORTE\n" + STATS, "Beta")
    result = build_creature_catalog(conn)
    assert result["coverage"]["candidate_statblocks"] == 2
    assert result["coverage"]["unparsed_pages"] == [2]
    assert not result["coverage"]["complete_for_detected_statblocks"]


def test_ranking_preserves_ties_filters_and_counts_full_catalog(catalog_book):
    conn, append, _ = catalog_book
    for name, element, vd in [
        ("ALFA", "SANGUE", 100), ("BETA", "SANGUE", 100),
        ("GAMA", "SANGUE", 20), ("DELTA", "MORTE", 80),
    ]:
        append(f"{name}\n{element}\nVD{vd}" + STATS, name.title())
    result = select_creatures(conn)
    assert len(result["sources"]) == 3
    assert result["structured_catalog"]["coverage"]["parsed_statblocks"] == 4
    assert len(select_creatures(conn, ranking="highest")["sources"]) == 2
    filtered = select_creatures(conn, element="sangue", ranking="highest")
    assert len(filtered["sources"]) == 2
    limited = select_creatures(conn, ranking="highest", limit=1)
    assert limited["sources"] == []  # Não esconder um empate para respeitar limite.
    assert limited["structured_catalog"]["truncated"] is True
    listing = select_creatures(conn, ranking="all", limit=2)
    assert len(listing["sources"]) == 2
    assert listing["structured_catalog"]["total_matching"] == 4


def test_sources_are_short_canonical_excerpts_and_registry_kinds_do_not_collide(catalog_book):
    conn, append, _ = catalog_book
    text = "ALFA\nSANGUE\n" + STATS + ("Texto de habilidade. " * 100) + "\nVD0400"
    append(text, "Alfa")
    source = select_creatures(conn)["sources"][0]
    assert len(source["text"]) < 100
    assert "\n[…]\n" in source["text"]
    assert all(excerpt in text for excerpt in source["text"].split("\n[…]\n"))
    assert source["printed_page"] == "8"
    registry = SourceRegistry()
    assert registry.add(source)["citation_id"] == 1
    assert registry.add({**source, "source_kind": "page", "text": text})["citation_id"] == 2
    assert registry.add({**source, "source_kind": "chunk", "text": "ALFA"})["citation_id"] == 3
    assert registry.add(source)["reused"] is True


def test_profile_guard_empty_book_and_incomplete_import(catalog_book):
    conn, append, _ = catalog_book
    empty = build_creature_catalog(conn)
    assert not empty["coverage"]["complete_for_detected_statblocks"]
    append("ALFA\nSANGUE\nVD40" + STATS, "Alfa")
    with conn:
        conn.execute("UPDATE books SET page_count=2")
    assert not build_creature_catalog(conn)["coverage"]["complete_for_detected_statblocks"]
    with conn:
        conn.execute("UPDATE books SET edition='2.0'")
    assert build_creature_catalog(conn)["entries"] == []


def test_catalog_works_with_read_only_connection_and_does_not_mutate(catalog_book):
    conn, append, path = catalog_book
    append("ECO\nMORTE\nVD40" + STATS, "Eco")
    before = conn.total_changes
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as readonly:
        readonly.row_factory = sqlite3.Row
        assert select_creatures(readonly)["structured_catalog"]["coverage"]["parsed_statblocks"] == 1
    assert conn.total_changes == before


def test_catalog_appends_canonical_indicator_definition_without_changing_entries(catalog_book):
    conn, append, _ = catalog_book
    # Uma expansão anterior existe, mas o bloco de definição deve ser preferido.
    append("Use o valor de desafio (VD) para comparar o encontro.", "Combates")
    for name, element in [("ALFA", "SANGUE"), ("BETA", "MORTE"), ("GAMA", "ENERGIA"),
                          ("DELTA", "CONHECIMENTO"), ("ÉPSILON", "MEDO")]:
        append(f"{name}\n{element}\nVD80" + STATS, name.title())
    definition_text = "Nome e VD\nO nome do adversário. Ao lado consta o valor\nde desafio dele."
    append("Introdução.\n" + definition_text + "\nDefesa\nOutro campo da ficha.", "Campos da ficha")
    result = select_creatures(conn)
    metadata = result["structured_catalog"]
    assert len(metadata["entries"]) == 5
    assert len(result["sources"]) == 6
    assert metadata["coverage"]["parsed_statblocks"] == 5
    definition = metadata["criterion_definition"]
    assert definition["term"] == "VD"
    assert definition["label"] == "valor de desafio"
    assert definition["citation_id"] == 6
    assert definition["pdf_page"] == 7  # Derivada do conteúdo, não da posição no livro real.
    assert definition["source_id"] == result["sources"][-1]["id"]
    assert result["sources"][-1]["text"] == definition_text
    assert "Outro campo" not in result["sources"][-1]["text"]


def test_indicator_definition_uses_expansion_fallback_but_not_unrelated_mention(catalog_book):
    conn, append, _ = catalog_book
    append("ALFA\nSANGUE\nVD40" + STATS, "Alfa")
    append("Este exemplo menciona valor de desafio, mas não define uma sigla.", "Introdução")
    missing = select_creatures(conn)
    assert missing["structured_catalog"]["criterion_definition"] is None
    assert len(missing["sources"]) == 1
    append("Use o valor\nde desafio (VD) do encontro.", "Combates")
    found = select_creatures(conn)
    assert found["structured_catalog"]["criterion_definition"]["citation_id"] == 2
    assert found["sources"][-1]["text"] == "valor\nde desafio (VD)"
    assert found["sources"][-1]["pdf_page"] == 3


@pytest.mark.parametrize("query,expected", [
    ("qual a criatura mais forte de cada elemento?", (None, "highest_per_element")),
    ("quais entidades mais poderosas por elemento", (None, "highest_per_element")),
    ("qual criatura tem maior VD de sangue?", ("Sangue", "highest")),
    ("liste todas as criaturas de morte por VD", ("Morte", "all")),
    ("quais os maiores VD de energia", ("Energia", "highest")),
    ("qual a criatura mais forte?", (None, "highest")),
    ("Qual criatura de Sangue tem maior VD, exceto o Diabo?", None),
    ("Qual criatura de Sangue tem maior VD, tirando o Diabo?", None),
    ("Quais são as três criaturas de Sangue com maior VD?", None),
    ("Quais são as 3 criaturas de Sangue com maior VD?", None),
    ("Qual é a entidade mais forte sem considerar VD?", None),
    ("Qual criatura é mais forte? Não use VD como critério.", None),
    ("E qual delas é a criatura mais forte?", None),
    ("Entre essas entidades, qual tem maior VD?", None),
    ("Qual entidade tem maior VD, A ou B?", None),
    ("Liste criaturas de sangue acima de VD 100", None),
    ("explique o Anfitrião", None),
    ("crie uma missão com as criaturas mais fortes", None),
    ("qual criatura tem maior dano?", None),
    ("qual a entidade mais poderosa narrativamente?", None),
    ("qual o ritual mais poderoso?", None),
    ("quais as criaturas de Sangue e Morte com maior VD?", None),
])
def test_intent_is_conservative_and_has_no_answer_name_rules(query, expected):
    intent = creature_catalog_intent(query)
    assert (intent["element"], intent["ranking"]) == expected if expected else intent is None


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": True}, {"limit": 101}, {"ranking": "lowest"},
    {"element": "luz"}, {"element": 123},
])
def test_selection_rejects_unsupported_arguments(catalog_book, kwargs):
    with pytest.raises((ValueError, TypeError)):
        select_creatures(catalog_book[0], **kwargs)
