import pytest

from rpg_assistente.book_tools import BookTools, _search_preview
from rpg_assistente.config import Settings
from rpg_assistente.db import connect


def test_matching_title_after_unrelated_rule_is_visible_with_original_accents():
    text = (
        "Outra regra fala de equipamentos comuns e reparos. " * 12
        + "Proteção Etérea\nA barreira dura uma rodada e exige concentração. "
        + "Outras condições precisam ser conferidas na página inteira. " * 8
    )
    assert "Proteção Etérea" not in text[:400]
    preview = _search_preview(text, "protecao eterea")
    assert "Proteção Etérea" in preview
    assert "exige concentração" in preview
    assert preview in text
    assert 0 < len(preview) <= 400
    assert preview != text[:400]


def test_multiple_query_terms_outweigh_an_early_incidental_word():
    text = (
        "Energia é mencionada em outra regra. "
        + "Texto de um assunto diferente. " * 20
        + "Pulso de Energia\nEste efeito exige toque e tem resistência parcial. "
        + "Condições adicionais. " * 15
    )
    preview = _search_preview(text, "pulso de energia")
    assert "Pulso de Energia" in preview
    assert "exige toque" in preview
    assert preview in text
    assert len(preview) <= 400


@pytest.mark.parametrize("query", ["deslocamento", "qual é", "   "])
def test_semantic_result_without_a_lexical_match_keeps_a_bounded_canonical_prefix(query):
    text = "Uma regra sem os termos usados na consulta. " * 20
    preview = _search_preview(text, query)
    assert text.startswith(preview)
    assert 0 < len(preview) <= 400


def test_short_result_is_not_rewritten_or_given_artificial_markers():
    text = "Regra curta.\nExige concentração."
    assert _search_preview(text, "concentracao") == text


@pytest.mark.parametrize("marker", ["b", "•", "-", "*"])
def test_list_preview_does_not_attach_a_previous_power_requirement(marker):
    text = (
        f"{marker} Golpe Preciso. " + "Descrição da opção anterior. " * 10
        + "Pré-requisitos: Agilidade 2 e treinamento em Ataque.\n"
        + f"{marker} Golpe Radiante. Seu dano aumenta em um dado.\n"
        + f"{marker} Defesa Rápida. Receba um bônus. Pré-requisito: nível 8.\n"
        + f"{marker} Outra Opção. " + "Condições adicionais. " * 15
    )
    preview = _search_preview(text, "golpe radiante requisitos")
    assert preview == f"{marker} Golpe Radiante. Seu dano aumenta em um dado."
    assert preview in text
    assert "Agilidade" not in preview
    assert "nível 8" not in preview


def test_long_list_entry_keeps_its_heading_when_the_match_fits():
    text = (
        "b Opção Anterior. Exige treinamento.\n"
        + "b Campo Protetor. " + "Mantenha concentração. " * 30
        + "\nb Próximo Poder. Exige nível 10."
    )
    preview = _search_preview(text, "campo protetor")
    assert preview.startswith("b Campo Protetor.")
    assert preview in text
    assert len(preview) <= 400
    assert "Opção Anterior" not in preview
    assert "Próximo Poder" not in preview


def test_tool_keeps_sqlite_source_citation_and_truncation_metadata(tmp_path):
    text = (
        "Descrição de uma opção anterior. " * 20
        + "Pulso Etéreo\nExige uma ação completa e concentração. "
        + "Condições preservadas no livro. " * 10
    )
    conn = connect(tmp_path / "book.sqlite3")
    try:
        with conn:
            book_id = conn.execute(
                "INSERT INTO books(title,edition,pdf_path,sha256,page_count) VALUES (?,?,?,?,?)",
                ("Livro de demonstração", "1", "private.pdf", "hash", 1),
            ).lastrowid
            page_id = conn.execute(
                "INSERT INTO pages(book_id,pdf_page,printed_page,chapter,section,text) "
                "VALUES (?,?,?,?,?,?)",
                (book_id, 1, "8", "Poderes", "Descrições", text),
            ).lastrowid
            chunk_id = conn.execute(
                "INSERT INTO chunks(page_id,position,text) VALUES (?,?,?)", (page_id, 0, text),
            ).lastrowid
        settings = Settings(tmp_path / "book.sqlite3", "", "", 0, 100, 10, None, None, False)

        def retrieve(*args, **kwargs):
            return {
                "sources": [{"id": chunk_id, "text": "Texto externo não canônico."}],
                "warnings": [],
            }

        tools = BookTools(conn, settings, retriever=retrieve)
        result = tools.search_book("pulso etereo")
        source = result["sources"][0]
        assert "Pulso Etéreo" in source["text"]
        assert source["text"] in text
        assert source["id"] == chunk_id
        assert source["page_id"] == page_id
        assert source["pdf_page"] == 1 and source["printed_page"] == "8"
        assert source["source_kind"] == "chunk"
        assert source["truncated"] is True
        assert source["original_chars"] == len(text)
        assert source["returned_chars"] == len(source["text"]) <= 400
        assert "private.pdf" not in str(result)
        repeated = tools.search_book("pulso etereo")["sources"][0]
        assert repeated["citation_id"] == source["citation_id"]
        assert repeated["text"] == source["text"]
    finally:
        conn.close()
