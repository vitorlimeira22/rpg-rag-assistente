from contextlib import closing
from dataclasses import replace
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from rpg_assistente import api, sheets
from rpg_assistente.config import Settings
from rpg_assistente.db import connect
from rpg_assistente.sheet_models import SheetData, validate_sheet


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = Settings(db_path=tmp_path / "db.sqlite", groq_api_key="fake", groq_model="test",
                        max_calls_per_day=5, max_output_tokens=2048, timeout_seconds=10,
                        input_usd_per_million=None, output_usd_per_million=None,
                        allow_private_book_to_groq=True)
    with closing(connect(settings.db_path)) as conn, conn:
        conn.execute("INSERT INTO books(title,edition,pdf_path,sha256,page_count) VALUES ('Ordem Paranormal','1.1','private.pdf','hash',330)")
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    with TestClient(api.app, base_url="http://localhost") as client:
        yield client, settings


def test_partial_drafts_and_revision_conflict_preserve_manual_edits(env):
    client, _ = env
    created = client.post("/api/sheets", json={})
    assert created.status_code == 201
    record = created.json()
    assert record["validation"]["valid"] is False
    data = {**record["data"], "name": "Manual", "derived": {"pv": 999}}
    saved = client.put(f"/api/sheets/{record['id']}", json={"revision": 1, "data": data})
    assert saved.status_code == 200
    assert saved.json()["data"]["derived"] != {"pv": 999}
    stale = client.put(f"/api/sheets/{record['id']}", json={"revision": 1, "data": {**data, "name": "stale"}})
    assert stale.status_code == 409
    assert client.get(f"/api/sheets/{record['id']}").json()["data"]["name"] == "Manual"


def test_chat_idempotency_and_changes_use_saved_state(env, monkeypatch):
    client, _ = env
    record = client.post("/api/sheets", json={"name": "Antes"}).json()
    calls = []
    def generator(conn, content, sheet, history, settings):
        calls.append(sheet.name)
        return {"status": "draft", "answer": "Nome alterado.", "sources": [],
                "proposal": {**sheet.model_dump(), "name": "Depois"}}
    monkeypatch.setattr(api, "generate_sheet_reply", generator)
    payload = {"revision": 1, "content": "Mude o nome", "request_id": str(uuid4())}
    url = f"/api/sheets/{record['id']}/chat"
    response = client.post(url, json=payload)
    assert response.status_code == 200
    assert response.json()["sheet"]["data"]["name"] == "Depois"
    assert client.post(url, json=payload).status_code == 200
    assert calls == ["Antes"]
    assert len(client.get(f"/api/sheets/{record['id']}").json()["messages"]) == 2
    assert client.post(url, json={**payload, "content": "outro"}).status_code == 409


def test_pending_creation_context_is_invalidated_by_manual_edit(env,monkeypatch):
    client,_=env
    record=client.post("/api/sheets",json={}).json()
    histories=[]
    def generator(conn,content,sheet,history,settings):
        histories.append(history)
        return {"status":"api_error","answer":"Interrompido","sources":[],
                "creation_task":{"requested":{"character_class":"especialista","nex":10},"request":"Crie uma ficha"}}
    monkeypatch.setattr(api,"generate_sheet_reply",generator)
    url=f"/api/sheets/{record['id']}/chat"
    for text in ("Crie uma ficha","Termine a criação"):
        assert client.post(url,json={"revision":1,"content":text,"request_id":str(uuid4())}).status_code == 200
    assert any(m.get("creation_task") for m in histories[-1])
    updated=client.put(f"/api/sheets/{record['id']}",json={"revision":1,"data":{**record['data'],"name":"Edição manual"}}).json()
    assert client.post(url,json={"revision":updated['revision'],"content":"Continue","request_id":str(uuid4())}).status_code == 200
    assert not any(m.get("creation_task") for m in histories[-1])


def test_manual_edit_during_generation_wins(env, monkeypatch):
    client, settings = env
    record = client.post("/api/sheets", json={"name": "Inicial"}).json()
    def generator(conn, content, sheet, history, unused):
        with closing(connect(settings.db_path)) as other:
            sheets.update_sheet(other, record["id"], sheets.SheetUpdate(revision=1, data=SheetData(name="Edição em outra aba")))
        return {"status": "draft", "answer": "Resposta atrasada", "sources": [],
                "proposal": {**sheet.model_dump(), "name": "IA antiga"}}
    monkeypatch.setattr(api, "generate_sheet_reply", generator)
    response = client.post(f"/api/sheets/{record['id']}/chat", json={"revision": 1, "content": "Sugira nome", "request_id": str(uuid4())}).json()
    assert response["result"]["status"] == "conflict"
    assert response["sheet"]["data"]["name"] == "Edição em outra aba"


def test_book_changed_during_generation_discards_answer(env, monkeypatch):
    client, settings = env
    record = client.post("/api/sheets", json={}).json()
    def generator(conn, content, sheet, history, unused):
        with closing(connect(settings.db_path)) as other, other:
            other.execute("UPDATE books SET sha256='changed'")
        return {"status": "draft", "answer": "old answer", "sources": [{"text": "old"}],
                "proposal": {**sheet.model_dump(), "name": "Não aplicar"}}
    monkeypatch.setattr(api, "generate_sheet_reply", generator)
    response = client.post(f"/api/sheets/{record['id']}/chat", json={"revision": 1, "content": "Criar", "request_id": str(uuid4())}).json()
    assert response["result"]["status"] == "book_changed"
    assert response["result"]["sources"] == []
    assert response["sheet"]["revision"] == 1


@pytest.mark.parametrize("cls,nex,expected", [("combatente",15,(31,9,18)), ("especialista",10,(21,8,20)), ("ocultista",15,(19,15,30))])
def test_progression_bases_for_distinct_classes(cls, nex, expected):
    sheet = SheetData(character_class=cls, nex=nex, attributes={"forca": 2,"agilidade": 3,"intelecto": 2,"presenca": 1,"vigor": 1})
    report = validate_sheet(sheet)
    values = report["derived"]
    assert (values["pv"],values["pe"],values["san"]) == expected
    assert values["pe_per_turn"] == nex // 5
    assert report["full_sheet_validated"] is False


def test_power_prerequisite_and_rank_limits_are_reported():
    sheet = SheetData(nex=15, attributes={"forca": 2,"agilidade": 3,"intelecto": 2,"presenca": 1,"vigor": 1},
                      powers=[{"name":"Proteção Pesada"}], equipment=[{"name":"Fuzil de assalto","category":2,"spaces":2}])
    errors = validate_sheet(sheet)["errors"]
    assert any("30%" in e["message"] for e in errors)
    assert any("categoria 2" in e["message"] for e in errors)


def test_migration_is_idempotent_and_delete_does_not_resurrect(env):
    client, settings = env
    with closing(connect(settings.db_path)) as conn, conn:
        conn.execute("INSERT INTO characters(name,character_class,data_json) VALUES (?,?,?)", ("Legado","combatente",json.dumps(SheetData(name="Legado").model_dump())))
    first = client.get("/api/sheets").json()
    assert len(first) == len(client.get("/api/sheets").json()) == 1
    assert client.delete(f"/api/sheets/{first[0]['id']}", headers={"Content-Type":"application/json"}).status_code == 204
    assert client.get("/api/sheets").json() == []
