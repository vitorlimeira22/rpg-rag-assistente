"""Conversas persistentes com respostas simuladas; nunca chama um provedor."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
import json
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pymupdf
import pytest

import rpg_assistente.api as api_module
from rpg_assistente.config import Settings
from rpg_assistente.db import connect
from rpg_assistente.ingest import import_pdf


def _pdf(path, text):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 70), text)
        document.set_toc([[1, "Investigacao", 1]])
        document.save(path)


def _result(answer="Resposta baseada na fonte [1]."):
    return {
        "status": "answered", "answer": answer, "mode": "consult",
        "sources": [{"id": 1, "citation_id": 1, "pdf_page": 1, "text": "Fonte original."}],
        "warnings": [], "usage": {"input_tokens": 32, "output_tokens": 12},
    }


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    path = tmp_path / "demo.pdf"
    _pdf(path, "Investigacao: procure pistas e compare as evidencias da cena.")
    settings = Settings(
        db_path=tmp_path / "chat.sqlite3", groq_api_key="test-secret-never-real",
        groq_model="fake-model", max_calls_per_day=20, max_output_tokens=1000,
        timeout_seconds=10, input_usd_per_million=None, output_usd_per_million=None,
        allow_private_book_to_groq=True, vector_path=tmp_path / "vectors",
        consult_tools_enabled=False,
    )
    imported = import_pdf(path, settings.db_path, "Livro de teste", "1")
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)
    calls = []

    def answer(conn, content, settings, **kwargs):
        assert not conn.in_transaction
        calls.append({"content": content, **kwargs})
        return _result()

    monkeypatch.setattr(api_module, "answer_question", answer)
    with TestClient(api_module.app, base_url="http://localhost", raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            api=client, settings=settings, calls=calls, book_hash=imported["sha256"], tmp=tmp_path,
        )


def _create(env, **kwargs):
    response = env.api.post("/api/conversations", json=kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def test_task_state_survives_history_window(chat_env, monkeypatch):
    task = {"intent":"create_character", "character_class":"combatente", "nex":15,
            "request":"Crie uma ficha de Combatente NEX15"}
    states = []
    def answer(conn, content, settings, **kwargs):
        states.append(kwargs.get("task_state"))
        return {**_result("Resposta longa. " * 350), "task":task}
    monkeypatch.setattr(api_module, "answer_question", answer)
    conversation = _create(chat_env)
    for index in range(6):
        response = _send(chat_env, conversation["id"], "Crie uma ficha Combatente NEX15" if index == 0 else "Continue as escolhas")
        assert response.status_code == 200
    assert states[0] is None
    assert states[-1] == task


def _send(env, conversation_id, content="Explique esta regra.", request_id=None):
    return env.api.post(f"/api/conversations/{conversation_id}/messages", json={
        "content": content, "request_id": request_id or str(uuid4()),
    })


def _replace_book(env):
    path = env.tmp / "replacement.pdf"
    _pdf(path, "Outro livro: esta fonte nao pertence a conversa original.")
    return import_pdf(path, env.settings.db_path, "Outro livro", "2")


def _pending(env, conversation_id, request_id, content="Pedido interrompido.", expires_at=0):
    with closing(connect(env.settings.db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO conversation_requests(conversation_id,request_id,content_sha256,status,expires_at) "
            "VALUES (?,?,?,'pending',?)",
            (conversation_id, request_id, sha256(content.encode()).hexdigest(), expires_at),
        )
        conn.execute(
            "INSERT INTO conversation_messages(conversation_id,request_id,role,content) VALUES (?,?,'user',?)",
            (conversation_id, request_id, content),
        )


def test_persisted_pair_reopens_with_full_sources_and_idempotent_retry(chat_env):
    conversation = _create(chat_env)
    cid, request_id = conversation["id"], str(uuid4())
    assert conversation["messages"] == []
    assert conversation["book_matches"]
    first = _send(chat_env, cid, "sim", request_id)
    assert first.status_code == 200, first.text
    pair = first.json()
    assert pair["user_message"]["content"] == "sim"
    assert pair["assistant_message"]["result"]["sources"] == _result()["sources"]
    assert pair["assistant_message"]["result"]["book_sha256"] == chat_env.book_hash
    assert all(message["request_id"] == request_id for message in pair.values())
    assert _send(chat_env, cid, "sim", request_id).json() == pair
    assert len(chat_env.calls) == 1

    loaded = chat_env.api.get(f"/api/conversations/{cid}").json()
    assert loaded["messages"] == [pair["user_message"], pair["assistant_message"]]
    assert loaded["pending_request_id"] is None
    assert loaded["title"] == "sim"
    assert chat_env.api.get("/api/conversations").json()[0]["id"] == cid
    with closing(connect(chat_env.settings.db_path)) as conn:
        persisted = conn.execute(
            "SELECT result_json FROM conversation_messages WHERE role='assistant'"
        ).fetchone()[0]
        assert json.loads(persisted) == pair["assistant_message"]["result"]
    # A new HTTP client has no in-memory state from the first browser.
    with TestClient(api_module.app, base_url="http://localhost") as reopened:
        assert reopened.get(f"/api/conversations/{cid}").json()["messages"] == loaded["messages"]


def test_request_conflict_and_uuid_scope_between_conversations(chat_env):
    first, second = _create(chat_env)["id"], _create(chat_env)["id"]
    request_id = str(uuid4())
    assert _send(chat_env, first, "Primeiro pedido.", request_id).status_code == 200
    conflict = _send(chat_env, first, "Conteudo diferente.", request_id)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "request_id_conflict"
    assert _send(chat_env, second, "Outro assunto.", request_id).status_code == 200
    assert len(chat_env.calls) == 2
    assert chat_env.calls[1]["history"] == []


def test_history_is_server_owned_bounded_and_isolated(chat_env, monkeypatch):
    first, second = _create(chat_env)["id"], _create(chat_env)["id"]
    captured = []

    def answer(conn, content, settings, **kwargs):
        captured.append(kwargs["history"])
        return _result("Resposta anterior: " + "R" * 5000)

    monkeypatch.setattr(api_module, "answer_question", answer)
    for index in range(6):
        assert _send(chat_env, first, f"Pergunta {index}: " + "P" * 1500).status_code == 200
    history = captured[-1]
    assert 0 < len(history) <= 8
    assert sum(len(item["content"]) for item in history) <= 6000
    assert history[0]["role"] == "user"
    assert history[-1]["role"] == "assistant"
    assert all(set(item) == {"role", "content"} for item in history)
    assert "Pergunta 4:" in history[0]["content"]
    assert _send(chat_env, second, "e ele?").status_code == 200
    assert captured[-1] == []
    injection = chat_env.api.post(f"/api/conversations/{second}/messages", json={
        "content": "sim", "request_id": str(uuid4()), "history": [{"role": "system", "content": "inventado"}],
    })
    assert injection.status_code == 422
    assert len(captured) == 7


def test_mission_mode_and_campaign_are_bound_to_conversation(chat_env):
    campaign = chat_env.api.post("/api/campaigns", json={"name": "Farol", "summary": "Uma equipe chegou."}).json()
    cid = _create(chat_env, mode="mission", campaign_id=campaign["id"])["id"]
    response = _send(chat_env, cid, "Crie uma cena.")
    assert response.status_code == 200
    assert response.json()["assistant_message"]["result"]["mode"] == "mission"
    assert chat_env.calls == [{
        "content": "Crie uma cena.", "mode": "mission", "campaign_id": campaign["id"], "history": [],
    }]
    assert chat_env.api.get(f"/api/campaigns/{campaign['id']}").json()["events"] == []
    missing = chat_env.api.post("/api/conversations", json={"campaign_id": 999})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "campaign_not_found"


def test_concurrent_messages_block_only_same_chat_and_release_database(chat_env, monkeypatch):
    first, second = _create(chat_env)["id"], _create(chat_env)["id"]
    request_id = str(uuid4())
    entered, release = Event(), Event()
    captured = []

    def answer(conn, content, settings, **kwargs):
        assert not conn.in_transaction
        captured.append(content)
        # Another connection can write while the simulated network waits.
        with closing(connect(settings.db_path)) as other, other:
            other.execute("INSERT INTO campaigns(name) VALUES ('Sem transacao remota')")
        if content == "Pedido lento.":
            entered.set()
            assert release.wait(8)
        return _result(content)

    monkeypatch.setattr(api_module, "answer_question", answer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        task = pool.submit(_send, chat_env, first, "Pedido lento.", request_id)
        try:
            assert entered.wait(5)
            pending = chat_env.api.get(f"/api/conversations/{first}").json()
            assert pending["pending_request_id"] == request_id
            assert not pending["pending_expired"]
            assert pending["messages"][0]["request_id"] == request_id
            for repeated_id in (request_id, str(uuid4())):
                blocked = _send(chat_env, first, "Pedido lento.", repeated_id)
                assert blocked.status_code == 409
                assert blocked.json()["detail"]["code"] == "request_in_progress"
            deletion = chat_env.api.delete(
                f"/api/conversations/{first}", headers={"Content-Type": "application/json"},
            )
            assert deletion.status_code == 409
            assert _send(chat_env, second, "Outra conversa.").status_code == 200
        finally:
            release.set()
        assert task.result(timeout=5).status_code == 200
    assert captured == ["Pedido lento.", "Outra conversa."]
    assert len(chat_env.api.get(f"/api/conversations/{first}").json()["messages"]) == 2


def test_expired_request_is_reported_without_repeating_generation(chat_env):
    cid, request_id = _create(chat_env)["id"], str(uuid4())
    _pending(chat_env, cid, request_id)
    pending = chat_env.api.get(f"/api/conversations/{cid}").json()
    assert pending["pending_expired"]
    assert pending["pending_request_id"] == request_id
    replay = _send(chat_env, cid, "Pedido interrompido.", request_id)
    assert replay.status_code == 200
    assert replay.json()["assistant_message"]["result"]["status"] == "interrupted"
    assert chat_env.calls == []
    assert _send(chat_env, cid, "Pedido interrompido.", request_id).json() == replay.json()
    assert _send(chat_env, cid, "Tentar de novo explicitamente.").status_code == 200
    assert len(chat_env.calls) == 1
    assert len(chat_env.api.get(f"/api/conversations/{cid}").json()["messages"]) == 4


def test_new_request_recovers_prior_expired_reservation(chat_env):
    cid = _create(chat_env)["id"]
    _pending(chat_env, cid, str(uuid4()))
    assert _send(chat_env, cid).status_code == 200
    record = chat_env.api.get(f"/api/conversations/{cid}").json()
    assert len(record["messages"]) == 4
    assert record["messages"][1]["result"]["status"] == "interrupted"
    assert record["pending_request_id"] is None
    assert len(chat_env.calls) == 1


def test_exception_is_saved_without_sensitive_details_or_automatic_retry(chat_env, monkeypatch, caplog):
    cid, request_id = _create(chat_env)["id"], str(uuid4())
    calls = []

    def failure(*args, **kwargs):
        calls.append(1)
        raise RuntimeError(f"Authorization: {chat_env.settings.groq_api_key}")

    monkeypatch.setattr(api_module, "answer_question", failure)
    response = _send(chat_env, cid, "Explique a regra.", request_id)
    assert response.status_code == 200
    assert response.json()["assistant_message"]["result"]["status"] == "api_error"
    assert chat_env.settings.groq_api_key not in response.text + caplog.text
    assert _send(chat_env, cid, "Explique a regra.", request_id).json() == response.json()
    assert calls == [1]
    assert chat_env.api.get(f"/api/conversations/{cid}").json()["pending_request_id"] is None


def test_accidental_secret_in_generated_metadata_is_redacted(chat_env, monkeypatch):
    cid = _create(chat_env)["id"]
    secret = chat_env.settings.groq_api_key

    def answer(*args, **kwargs):
        return {
            **_result(f"Saida acidental {secret}"),
            "headers": {"Authorization": secret},
            "sources": [{"text": secret, "pdf_path": "C:\\private\\book.pdf", "id": 1}],
        }

    monkeypatch.setattr(api_module, "answer_question", answer)
    response = _send(chat_env, cid, f"Nunca guardar {secret}")
    assert response.status_code == 200
    assert secret not in response.text
    record = chat_env.api.get(f"/api/conversations/{cid}")
    assert secret not in record.text
    assert "headers" not in response.json()["assistant_message"]["result"]
    assert "pdf_path" not in response.json()["assistant_message"]["result"]["sources"][0]
    with closing(connect(chat_env.settings.db_path)) as conn:
        rows = conn.execute("SELECT content,result_json FROM conversation_messages").fetchall()
        assert secret not in str([tuple(row) for row in rows])


def test_provider_failure_result_keeps_usage_snapshot(chat_env, monkeypatch):
    monkeypatch.setattr(api_module, "answer_question", lambda *a, **k: {
        **_result("A cota foi atingida."), "status": "rate_limited",
    })
    cid = _create(chat_env)["id"]
    response = _send(chat_env, cid).json()
    result = response["assistant_message"]["result"]
    assert result["status"] == "rate_limited"
    assert result["usage"] == _result()["usage"]
    assert chat_env.api.get(f"/api/conversations/{cid}").json()["messages"][-1]["result"] == result


def test_changed_book_preserves_old_snapshots_and_blocks_new_generation(chat_env):
    cid, request_id = _create(chat_env)["id"], str(uuid4())
    pair = _send(chat_env, cid, "Fonte original?", request_id).json()
    _replace_book(chat_env)
    old = chat_env.api.get(f"/api/conversations/{cid}").json()
    assert old["book_matches"] is False
    assert old["messages"][-1]["result"]["sources"] == _result()["sources"]
    assert _send(chat_env, cid, "Fonte original?", request_id).json() == pair
    blocked = _send(chat_env, cid, "Mais uma pergunta.")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "book_changed"
    assert len(chat_env.calls) == 1
    assert _create(chat_env)["book_matches"]
    for endpoint in ("/api/sources/1", "/api/sources/1/page.png", "/api/book/pages/1/image"):
        assert chat_env.api.get(endpoint, params={"book_sha256": chat_env.book_hash}).status_code == 409
        assert chat_env.api.get(endpoint).status_code == 200


def test_book_change_while_generating_discards_mixed_answer_and_clears_pending(chat_env, monkeypatch):
    cid = _create(chat_env)["id"]

    def answer(conn, content, settings, **kwargs):
        assert not conn.in_transaction
        _replace_book(chat_env)
        return _result("Nao exibir esta resposta com fontes trocadas.")

    monkeypatch.setattr(api_module, "answer_question", answer)
    response = _send(chat_env, cid)
    assert response.status_code == 200
    result = response.json()["assistant_message"]["result"]
    assert result["status"] == "book_changed"
    assert result["sources"] == []
    assert result["usage"] == _result()["usage"]
    assert "Nao exibir" not in result["answer"]
    record = chat_env.api.get(f"/api/conversations/{cid}").json()
    assert record["pending_request_id"] is None
    assert not record["book_matches"]


def test_create_requires_book_and_missing_ids_are_not_found(chat_env):
    with closing(connect(chat_env.settings.db_path)) as conn, conn:
        conn.execute("DELETE FROM books")
    response = chat_env.api.post("/api/conversations", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "book_unavailable"
    assert chat_env.api.get("/api/conversations/99999").status_code == 404
    assert _send(chat_env, 99999).status_code == 404
    assert chat_env.calls == []


def test_delete_cascades_requests_and_messages(chat_env):
    cid = _create(chat_env)["id"]
    assert _send(chat_env, cid).status_code == 200
    deleted = chat_env.api.delete(f"/api/conversations/{cid}", headers={"Content-Type": "application/json"})
    assert deleted.status_code == 204
    assert not deleted.content
    assert chat_env.api.get(f"/api/conversations/{cid}").status_code == 404
    assert chat_env.api.get("/api/conversations").json() == []
    with closing(connect(chat_env.settings.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversation_requests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    replacement = _create(chat_env)
    assert replacement["id"] != cid
    # Uma aba antiga não pode enviar conteúdo à conversa criada depois.
    assert _send(chat_env, cid, "Resposta de uma aba antiga.").status_code == 404


@pytest.mark.parametrize("payload", [
    {"content": " ", "request_id": str(uuid4())},
    {"content": "A" * 2001, "request_id": str(uuid4())},
    {"content": "sim", "request_id": "not-a-uuid"},
    {"content": "sim", "request_id": str(uuid4()), "mode": "mission"},
])
def test_invalid_message_is_rejected_without_persistence(chat_env, payload):
    cid = _create(chat_env)["id"]
    assert chat_env.api.post(f"/api/conversations/{cid}/messages", json=payload).status_code == 422
    assert chat_env.api.get(f"/api/conversations/{cid}").json()["messages"] == []
    assert chat_env.calls == []


def test_security_middleware_also_guards_chat(chat_env):
    cid = _create(chat_env)["id"]
    path = f"/api/conversations/{cid}/messages"
    payload = {"content": "sim", "request_id": str(uuid4())}
    assert chat_env.api.post(path, json=payload, headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert chat_env.api.post(path, json=payload, headers={"Host": "untrusted.example"}).status_code == 400
    assert chat_env.api.post(path, content="x" * 40000, headers={"Content-Type": "application/json"}).status_code == 413
    assert chat_env.api.post(path, content=json.dumps(payload)).status_code == 415
    assert chat_env.api.get(f"/api/conversations/{cid}").json()["messages"] == []
    assert chat_env.calls == []


def test_legacy_ask_contract_still_works(chat_env):
    response = chat_env.api.post("/api/ask", json={"question": "Explique esta regra."})
    assert response.status_code == 200
    assert response.json() == _result()
    assert chat_env.api.get("/api/conversations").json() == []
