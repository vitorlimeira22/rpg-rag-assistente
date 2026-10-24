"""Ferramenta de cálculo, com páginas sintéticas e sem modelo ou rede."""
from contextlib import closing
from copy import deepcopy

import pytest

from rpg_assistente.character_tools import CHARACTER_TOOL_SCHEMAS, CharacterTools
from rpg_assistente.characters import character_options
from rpg_assistente.db import connect


@pytest.mark.parametrize("nex,valid", [(5, True), (35, False)])
def test_research_passes_python_result_and_citations_to_synthesis_and_client(
    rules_db, arguments, nex, valid, tmp_path, monkeypatch,
):
    import json
    from time import perf_counter
    from rpg_assistente.config import Settings
    from rpg_assistente.llm import GenerationTurn, ToolCall
    import rpg_assistente.research as research

    arguments["nex"] = nex
    settings = Settings(db_path=tmp_path / "unused.sqlite3", groq_api_key="fake",
                        groq_model="test", max_calls_per_day=5, max_output_tokens=1500,
                        timeout_seconds=10, input_usd_per_million=None,
                        output_usd_per_million=None, allow_private_book_to_groq=True,
                        consult_max_model_calls=2)
    sources = [CharacterTools(rules_db)._evidence[41]]
    messages_seen = []

    def generate(settings, messages, **kwargs):
        messages_seen.append(deepcopy(messages))
        if len(messages_seen) == 1:
            assert '"pv_base_formula": "12 + Vigor"' in messages[1]["content"]
            assert '"san_base": 20' in messages[1]["content"]
            assert "validate_basic_character" in {
                tool["function"]["name"] for tool in kwargs["tools"]
            }
            return GenerationTurn("", 100, 20, "tool_calls", [
                ToolCall("check", "validate_basic_character", json.dumps(arguments))
            ])
        assert kwargs["tools"] is None
        expected = '"pv_base": 13' if valid else '"derived": null'
        assert expected in messages[1]["content"]
        assert '"nex": ' + str(nex) in messages[1]["content"]
        return GenerationTurn("Conferência parcial com os dados informados [2].", 200, 40, "stop")

    monkeypatch.setattr(research, "generate_turn", generate)
    result = research.research_answer(
        rules_db, "Confira estes atributos.", settings, instructions="Use fontes.",
        prompt_version="test", initial={"sources": sources}, context="",
        context_warnings=[], overall_started=perf_counter(),
    )
    assert result["status"] == "answered"
    assert len(result["rule_checks"]) == 1
    assert len(result["structured_rules"]) == 1
    assert result["structured_rules"][0]["character_class"] == "ocultista"
    check = result["rule_checks"][0]
    assert check["arguments"] == arguments
    assert check["validation"]["valid"] is valid
    assert len(check["citation_ids"]) == 4
    assert all(any(source["citation_id"] == citation for source in result["sources"])
               for citation in check["citation_ids"])
    assert result["research"]["steps"] == [{
        "tool": "validate_basic_character", "status": "ok" if valid else "invalid_character",
    }]
    assert rules_db.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0


@pytest.fixture
def rules_db(tmp_path):
    with closing(connect(tmp_path / "rules.sqlite3")) as conn:
        with conn:
            book_id = conn.execute(
                "INSERT INTO books(title,edition,pdf_path,sha256,page_count) VALUES (?,?,?,?,?)",
                ("Ordem Paranormal RPG", "1.1", "unused.pdf", "a" * 64, 330),
            ).lastrowid
            for reference in character_options()["sources"]:
                page = reference["pdf_page"]
                if page == 22:
                    text = "GERAÇÃO\nDE ATRIBUTOS\nRegra demonstrativa dos cinco atributos.\nEXEMPLO\nDados sintéticos."
                elif page == 31:
                    text = "Limite de PE. Regra demonstrativa de PE por turno.\nTABELA\nDados sintéticos."
                elif page == 44:
                    text = "Defesa\nRegra demonstrativa da Defesa base.\nDeslocamento\nDados sintéticos."
                else:
                    text = "PONTOS DE VIDA INICIAIS\nBloco demonstrativo de classe.\nCARACTERÍSTICAS\nDados sintéticos."
                page_id = conn.execute(
                    "INSERT INTO pages(book_id,pdf_page,printed_page,chapter,section,text) VALUES (?,?,?,?,?,?)",
                    (book_id, page, reference["printed_page"], "Criação", reference["section"], text),
                ).lastrowid
                conn.execute("INSERT INTO chunks(page_id,position,text) VALUES (?,0,?)", (page_id, text))
        yield conn


@pytest.fixture
def arguments():
    return {
        "character_class": "ocultista", "nex": 5,
        "attributes": {"agilidade": 2, "forca": 1, "intelecto": 3, "presenca": 2, "vigor": 1},
    }


@pytest.mark.parametrize("character_class,pv,pe,san,page", [
    ("combatente", 21, 4, 12, 33),
    ("especialista", 17, 5, 16, 37),
    ("ocultista", 13, 6, 20, 41),
])
def test_python_bases_and_compact_canonical_evidence_without_writes(
    rules_db, arguments, character_class, pv, pe, san, page,
):
    arguments["character_class"] = character_class
    before = rules_db.total_changes
    tool = CharacterTools(rules_db)
    assert tool.available
    result = tool.execute("validate_basic_character", arguments)
    assert result["status"] == "ok"
    assert result["arguments"] == arguments
    assert result["validation"]["derived"] == {
        "pv_base": pv, "pe_base": pe, "san_base": san, "defesa_base": 12,
        "limite_pe_base": 1, "attribute_total": 9,
    }
    assert result["validation"]["scope"]["full_sheet_validated"] is False
    assert result["rule_pdf_pages"] == [22, 31, page, 44]
    assert result["book_sha256"] == "a" * 64
    for evidence in result["rule_evidence"]:
        source = rules_db.execute("SELECT text FROM pages WHERE id=?", (evidence["page_id"],)).fetchone()
        assert evidence["text"] in source["text"]
        assert "Dados sintéticos" not in evidence["text"]
        assert "pdf_path" not in evidence
    assert rules_db.total_changes == before
    assert not rules_db.in_transaction
    result["rule_evidence"][0]["text"] = "altered"
    assert tool.execute("validate_basic_character", arguments)["rule_evidence"][0]["text"] != "altered"


@pytest.mark.parametrize("kind", ["missing", "float", "boolean", "text", "extra", "nested_extra"])
def test_tool_requires_explicit_strict_inputs_without_silent_defaults(rules_db, arguments, kind):
    data = deepcopy(arguments)
    if kind == "missing":
        data.pop("nex")
    elif kind == "extra":
        data["pv_base"] = 999
    elif kind == "nested_extra":
        data["attributes"]["destreza"] = 2
    else:
        data["attributes"]["agilidade"] = {"float": 2.0, "boolean": True, "text": "2"}[kind]
    result = CharacterTools(rules_db).execute("validate_basic_character", data)
    assert result["status"] == "invalid"
    assert "validation" not in result and "rule_evidence" not in result


@pytest.mark.parametrize("nex", [0, 10, 50, 99])
def test_tool_does_not_certify_evolution(rules_db, arguments, nex):
    arguments["nex"] = nex
    result = CharacterTools(rules_db).execute("validate_basic_character", arguments)
    assert result["status"] == "invalid_character"
    assert result["validation"]["derived"] is None
    assert any(error["field"] == "nex" for error in result["validation"]["errors"])


def test_invalid_distribution_has_no_derived_values(rules_db, arguments):
    arguments["attributes"]["intelecto"] = 8
    result = CharacterTools(rules_db).execute("validate_basic_character", arguments)
    assert result["status"] == "invalid_character"
    assert result["validation"]["derived"] is None


@pytest.mark.parametrize("change", ["title", "edition", "page_label", "page_text", "no_book"])
def test_profile_rejects_other_books_and_unconfirmed_pages(rules_db, arguments, change):
    with rules_db:
        if change == "title":
            rules_db.execute("UPDATE books SET title='Outro RPG'")
        elif change == "edition":
            rules_db.execute("UPDATE books SET edition='1.2'")
        elif change == "page_label":
            rules_db.execute("UPDATE pages SET printed_page='99' WHERE pdf_page=22")
        elif change == "page_text":
            rules_db.execute("UPDATE pages SET text='Página sem regra reconhecida' WHERE pdf_page=22")
        else:
            rules_db.execute("DELETE FROM books")
    tool = CharacterTools(rules_db)
    assert not tool.available
    assert tool.execute("validate_basic_character", arguments)["status"] == "unavailable"


def test_tool_rechecks_book_identity_before_calculation(rules_db, arguments):
    tool = CharacterTools(rules_db)
    with rules_db:
        rules_db.execute("UPDATE books SET sha256=?", ("b" * 64,))
    assert tool.execute("validate_basic_character", arguments)["status"] == "book_changed"


def test_schema_requires_every_input_and_forbids_extra_arguments():
    schema = CHARACTER_TOOL_SCHEMAS[0]["function"]["parameters"]
    assert set(schema["required"]) == {"character_class", "nex", "attributes"}
    assert schema["additionalProperties"] is False
    attrs = schema["$defs"]["Attributes"]
    assert set(attrs["required"]) == {"agilidade", "forca", "intelecto", "presenca", "vigor"}
    assert attrs["additionalProperties"] is False


@pytest.mark.parametrize("character_class,page,pv_formula,pe_formula,san", [
    ("combatente", 33, "20 + Vigor", "2 + Presença", 12),
    ("especialista", 37, "16 + Vigor", "3 + Presença", 16),
    ("ocultista", 41, "12 + Vigor", "4 + Presença", 20),
])
def test_creation_rules_disambiguate_initial_values_without_assuming_attributes(
    rules_db, character_class, page, pv_formula, pe_formula, san,
):
    tool = CharacterTools(rules_db)
    source = {"pdf_page": page, "text": "PONTOS DE\n VIDA INICIAIS\nA cada novo nível de exposição"}
    result = tool.creation_rules([source, source])
    assert len(result["facts"]) == len(result["rule_evidence"]) == 1
    fact = result["facts"][0]
    assert fact["character_class"] == character_class
    assert fact["nex"] == 5 and fact["pdf_page"] == page
    assert fact["pv_base_formula"] == pv_formula
    assert fact["pe_base_formula"] == pe_formula
    assert fact["san_base"] == san
    assert "attributes" not in fact
    assert "sem aplicar atributos" in fact["scope"]
    assert result["rule_evidence"][0]["pdf_page"] == page
    text = rules_db.execute("SELECT text FROM pages WHERE pdf_page=?", (page,)).fetchone()["text"]
    assert result["rule_evidence"][0]["text"] in text


@pytest.mark.parametrize("source", [
    {"pdf_page": 33, "text": "Poder de combatente em NEX 15%."},
    {"pdf_page": 100, "text": "PONTOS DE VIDA INICIAIS"},
    {"pdf_page": 33, "text": None},
])
def test_creation_rules_require_both_class_page_and_initial_values_heading(rules_db, source):
    assert CharacterTools(rules_db).creation_rules([source]) == {"facts": [], "rule_evidence": []}


def test_creation_rules_fail_closed_when_book_is_changed_or_profile_unavailable(rules_db):
    source = {"pdf_page": 33, "text": "PONTOS DE VIDA INICIAIS"}
    tool = CharacterTools(rules_db)
    with rules_db:
        rules_db.execute("UPDATE books SET edition='1.2'")
    assert tool.creation_rules([source]) == {"facts": [], "rule_evidence": []}
    assert CharacterTools(rules_db).creation_rules([source]) == {"facts": [], "rule_evidence": []}
