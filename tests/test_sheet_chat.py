"""Orquestração e rejeição de propostas, sem rede nem livros privados."""
import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from rpg_assistente import character_evidence, sheet_chat
from rpg_assistente.config import Settings
from rpg_assistente.db import connect
from rpg_assistente.llm import GenerationError, GenerationTurn
from rpg_assistente.sheet_models import SheetData


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = Settings(db_path=tmp_path / "db.sqlite", groq_api_key="test", groq_model="fake",
                        max_calls_per_day=5, max_output_tokens=2048, timeout_seconds=10,
                        input_usd_per_million=None, output_usd_per_million=None,
                        allow_private_book_to_groq=True)
    conn = connect(settings.db_path)
    with conn:
        conn.execute("INSERT INTO books(title,edition,pdf_path,sha256,page_count) VALUES ('Ordem Paranormal RPG','1.1','demo.pdf','hash',1)")
    monkeypatch.setattr(character_evidence, "character_evidence", lambda *a, **kw: {
        "sources": [{"id":1, "citation_id":1, "pdf_page":22, "text":"Regras demonstrativas"}],
        "candidate_options": {}, "warnings": [], "coverage": {}, "retrieval": {"elapsed_ms":1},
    })
    yield conn, settings
    conn.close()


def turn(changes, text="Proposta [1]"):
    return GenerationTurn(text=json.dumps({"answer":text, "changes":changes}),
                          input_tokens=100, output_tokens=50, finish_reason="stop")


def test_one_bounded_repair_never_applies_rejected_changes(setup, monkeypatch):
    conn, settings = setup
    attempts = [turn({"name":"Errado", "derived":{"pv":999}}), turn({"name":"Corrigido"})]
    prompts = []
    def generate(s, messages, **kwargs):
        prompts.append(messages)
        return attempts.pop(0)
    monkeypatch.setattr(sheet_chat, "generate_turn", generate)
    result = sheet_chat.generate_sheet_reply(conn, "Mude o nome", SheetData(), [], settings)
    assert result["proposal"]["name"] == "Corrigido"
    assert result["proposal"]["nex"] == 5
    assert result["usage"]["call_count"] == 2
    assert "NÃO foi aplicada" in prompts[-1][-1]["content"]
    assert [row[0] for row in conn.execute("SELECT status FROM llm_calls ORDER BY id")] == ["invalid_proposal", "draft"]


def test_repair_retains_all_previous_proposed_fields(setup, monkeypatch):
    conn, settings = setup
    attempts = [turn({"name":"Agente", "player":"Rafa"}), turn({"notes":"Corrigido"})]
    reports = [{"errors":[{"field":"name", "message":"Revisar"}], "warnings":[]},
               {"errors":[{"field":"name", "message":"Novo erro"}], "warnings":[]},
               {"errors":[], "warnings":[]}]
    monkeypatch.setattr(sheet_chat, "generate_turn", lambda *a, **kw: attempts.pop(0))
    monkeypatch.setattr(sheet_chat, "validate_sheet", lambda *a: reports.pop(0))
    result = sheet_chat.generate_sheet_reply(conn, "Troque meus dados", SheetData(), [], settings)
    assert result["proposal"]["name"] == "Agente"
    assert result["proposal"]["player"] == "Rafa"
    assert result["proposal"]["notes"] == "Corrigido"


def test_full_creation_cannot_pass_by_only_changing_equipment(setup, monkeypatch):
    conn, settings = setup
    monkeypatch.setattr(sheet_chat, "generate_turn", lambda *a, **kw: turn({"equipment":[{"name":"Faca", "category":0,"spaces":1,"quantity":1}]}))
    result = sheet_chat.generate_sheet_reply(conn, "Crie uma ficha completa", SheetData(), [], settings)
    assert result["status"] == "invalid_proposal"
    assert "proposal" not in result


def test_api_error_has_no_automatic_retry_and_preserves_sheet(setup, monkeypatch):
    conn, settings = setup
    calls = []
    def generate(*a, **kw):
        calls.append(True)
        raise GenerationError("Cota atingida",429)
    monkeypatch.setattr(sheet_chat, "generate_turn", generate)
    result = sheet_chat.generate_sheet_reply(conn, "Sugira uma ficha", SheetData(), [], settings)
    assert len(calls) == 1
    assert result["status"] == "api_error"
    assert "proposal" not in result
    assert conn.execute("SELECT status FROM llm_calls").fetchone()[0] == "api_error"


def test_two_bad_proposals_are_rejected_and_recorded(setup, monkeypatch):
    conn, settings = setup
    monkeypatch.setattr(sheet_chat, "generate_turn", lambda *a, **kw: turn({"derived":{"pv":999}}))
    result = sheet_chat.generate_sheet_reply(conn, "Calcule PV", SheetData(), [], settings)
    assert result["status"] == "invalid_proposal"
    assert result["usage"]["call_count"] == 2
    assert "proposal" not in result


def test_invented_reference_is_rejected(setup, monkeypatch):
    conn, settings = setup
    monkeypatch.setattr(sheet_chat, "generate_turn", lambda *a, **kw: turn({"name":"Nome"},"Regra [999]"))
    assert "proposal" not in sheet_chat.generate_sheet_reply(conn, "Mude nome", SheetData(), [], settings)


def test_other_book_cannot_activate_ordem_rules(setup, monkeypatch):
    conn, settings = setup
    with conn:
        conn.execute("UPDATE books SET title='Outro sistema'")
    monkeypatch.setattr(sheet_chat, "generate_turn", lambda *a, **kw: pytest.fail("Não deveria chamar a API"))
    result = sheet_chat.generate_sheet_reply(conn, "Crie ficha", SheetData(), [], settings)
    assert result["status"] == "unsupported_book"
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0


def test_provider_json_mode_and_local_schema_accept_partial_updates(setup, monkeypatch):
    conn, settings = setup
    seen = []
    def generate(*args, **kwargs):
        seen.append(kwargs['response_format'])
        return turn({'name':'Novo nome'})
    monkeypatch.setattr(sheet_chat, 'generate_turn', generate)
    result = sheet_chat.generate_sheet_reply(conn, 'Mude o nome', SheetData(), [], settings)
    assert seen == [{'type':'json_object'}]
    assert result['proposal']['name'] == 'Novo nome'
    assert result['proposal']['attributes'] == SheetData().attributes.model_dump()


def test_rename_after_creation_does_not_regenerate_manual_fields(setup, monkeypatch):
    conn,settings=setup
    original=SheetData(name="Antes",notes="Regra da mesa anotada manualmente",skills=[{"name":"Profissão (detetive)","bonus":5}])
    monkeypatch.setattr(sheet_chat,"generate_turn",lambda *a,**kw:turn({"name":"Depois"}))
    result=sheet_chat.generate_sheet_reply(conn,"Troque somente o nome",original,
        [{"role":"user","content":"Crie uma ficha completa de Combatente NEX15"}],settings)
    assert result["proposal"]["skills"] == original.model_dump()["skills"]
    assert result["proposal"]["notes"] == original.notes
    assert result["proposal"]["powers"] == []


def test_book_notation_is_normalized_only_at_model_boundary():
    patch={"equipment":[{"name":"Pistola","category":"I","spaces":"1 espaço","quantity":"1"}]}
    normalized=sheet_chat._normalize_model_changes(patch,True)
    item=normalized["equipment"][0]
    assert item["category"] == 1 and item["spaces"] == 1 and item["quantity"] == 1
    assert SheetData.model_validate(normalized).equipment[0].category == 1


def test_pending_creation_keeps_explicit_class_nex_and_track_after_failure(setup,monkeypatch):
    conn,settings=setup
    monkeypatch.setattr(sheet_chat,"generate_turn",lambda *a,**kw:turn({
        "attributes":{"forca":1,"agilidade":1,"intelecto":3,"presenca":1,"vigor":3},"equipment":["Faca"]}))
    history=[{"role":"assistant","content":"Criação pendente", "creation_task":{
        "requested":{"character_class":"especialista","nex":10,"origin":"investigador","track":"tecnico"},
        "request":"Crie uma ficha de Especialista NEX10, origem Investigador, trilha Técnico"}}]
    result=sheet_chat.generate_sheet_reply(conn,"Essas informações já estão no livro; termine",SheetData(),history,settings,max_attempts=1)
    assert result["proposal"]["character_class"] == "especialista"
    assert result["proposal"]["nex"] == 10
    assert result["proposal"]["track"] == "tecnico"
