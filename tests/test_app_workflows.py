"""Fluxos públicos, limites e persistência: tudo temporário, sem API externa."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from threading import Barrier
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pymupdf
import pytest

import rpg_assistente.answer as answer_module
import rpg_assistente.api as api_module
from rpg_assistente.campaigns import campaign_context
from rpg_assistente.config import Settings
from rpg_assistente.db import connect, reserve_call
from rpg_assistente.ingest import import_pdf
from rpg_assistente.llm import Generation


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    pdf_path = tmp_path / "authorized-demo.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text(
            (50, 70),
            "Pericia de investigacao: procure pistas e compare as evidencias da cena.",
        )
        document.set_toc([[1, "Investigacao", 1]])
        document.save(pdf_path)
    settings = Settings(
        db_path=tmp_path / "local.sqlite3", groq_api_key="test-key-only",
        groq_model="test-model", max_calls_per_day=5, max_output_tokens=2048,
        timeout_seconds=10, input_usd_per_million=None, output_usd_per_million=None,
        allow_private_book_to_groq=True, vector_path=tmp_path / "chroma",
        consult_tools_enabled=False,
    )
    import_pdf(pdf_path, settings.db_path, "Livro demonstrativo autorizado", "1")
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)

    def unexpected_generation(*args, **kwargs):
        pytest.fail("O teste não autorizou uma geração, nem mesmo simulada.")

    monkeypatch.setattr(answer_module, "generate_reply", unexpected_generation)
    with TestClient(api_module.app, base_url="http://localhost", raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, settings=settings, pdf_path=pdf_path)


@pytest.fixture
def character_data():
    return {
        "name": "Agente de teste", "character_class": "especialista", "nex": 5,
        "attributes": {"agilidade": 2, "forca": 1, "intelecto": 3, "presenca": 2, "vigor": 1},
        "origin": "Investigador", "notes": "Conceito fictício para o teste.",
    }


def test_character_create_read_update_and_invalid_save(app_env, character_data):
    api = app_env.client
    created = api.post("/api/characters", json=character_data)
    assert created.status_code == 201
    record = created.json()
    assert record["validation"]["valid"]
    assert record["validation"]["scope"]["full_sheet_validated"] is False
    assert record["validation"]["derived"]["pv_base"] == 17
    character_id = record["id"]
    assert api.get(f"/api/characters/{character_id}").json()["notes"] == character_data["notes"]
    assert api.get("/api/characters").json()[0]["id"] == character_id

    changed = {**character_data, "name": "Nome revisado", "character_class": "combatente"}
    updated = api.put(f"/api/characters/{character_id}", json=changed)
    assert updated.status_code == 200
    assert updated.json()["validation"]["derived"]["pv_base"] == 21

    invalid = {**changed, "nex": 50}
    assert api.post("/api/characters/validate", json=invalid).json()["valid"] is False
    assert api.put(f"/api/characters/{character_id}", json=invalid).status_code == 422
    assert api.post("/api/characters", json=invalid).status_code == 422
    assert api.get(f"/api/characters/{character_id}").json()["nex"] == 5

    # Reabre SQLite de modo independente: o resultado não existe apenas na
    # memória do endpoint nem em um objeto mantido pelo navegador.
    with closing(connect(app_env.settings.db_path)) as conn:
        rows = conn.execute("SELECT name,data_json FROM characters").fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "Nome revisado"
        assert json.loads(rows[0]["data_json"])["character_class"] == "combatente"
    assert api.put("/api/characters/9999", json=character_data).status_code == 404
    assert api.get("/api/characters/9999").status_code == 404


def test_character_type_errors_do_not_echo_user_values(app_env, character_data):
    character_data["attributes"]["agilidade"] = "private-text-that-must-not-be-echoed"
    result = app_env.client.post("/api/characters", json=character_data)
    assert result.status_code == 422
    assert "private-text-that-must-not-be-echoed" not in result.text
    assert all("input" not in error for error in result.json()["detail"])
    assert app_env.client.get("/api/characters").json() == []


def test_campaign_persistence_events_and_narrative_context(app_env, monkeypatch):
    api = app_env.client
    campaign = api.post("/api/campaigns", json={
        "name": "  Farol de teste  ", "summary": "A médica Marina continua viva.",
        "house_rules": "Nesta mesa, os jogadores descrevem uma lembrança ao descansar.",
    })
    assert campaign.status_code == 201
    campaign_id = campaign.json()["id"]
    assert campaign.json()["name"] == "Farol de teste"
    for text in ("A equipe chegou ao farol.", "Marina entregou uma chave aos agentes."):
        assert api.post(f"/api/campaigns/{campaign_id}/events", json={"text": text}).status_code == 201
    before = api.get(f"/api/campaigns/{campaign_id}").json()
    assert len(before["events"]) == 2
    captured = []

    def generate(settings, instructions, prompt, client=None):
        captured.append((instructions, prompt))
        return Generation("Sugestão criativa: investiguem o porão. Consultem pistas [1].", 20, 15, "stop")

    monkeypatch.setattr(answer_module, "generate_reply", generate)
    result = api.post("/api/ask", json={
        "question": "Crie uma missão de investigação com pistas.",
        "mode": "mission", "campaign_id": campaign_id,
    })
    assert result.status_code == 200
    assert result.json()["mode"] == "mission"
    assert result.json()["status"] == "answered"
    instructions, prompt = captured[0]
    assert "Sugestão criativa" in instructions
    assert "Não transforme regras da mesa em regras oficiais" in instructions
    assert "A médica Marina continua viva." in prompt
    assert "não são regras oficiais" in prompt
    assert "uma lembrança ao descansar" in prompt
    assert prompt.index("A equipe chegou ao farol.") < prompt.index("Marina entregou uma chave")
    assert "PDF p. 1" in prompt
    assert api.get(f"/api/campaigns/{campaign_id}").json() == before

    updated = api.put(f"/api/campaigns/{campaign_id}", json={
        "name": "Farol revisado", "summary": "Marina foi resgatada.", "house_rules": "",
    })
    assert updated.status_code == 200
    assert len(updated.json()["events"]) == 2
    assert api.get("/api/campaigns").json()[0]["summary"] == "Marina foi resgatada."


def test_missing_campaign_and_blank_input_do_not_generate(app_env):
    api = app_env.client
    assert api.get("/api/campaigns/9999").status_code == 404
    assert api.post("/api/campaigns/9999/events", json={"text": "Acontecimento"}).status_code == 404
    assert api.put("/api/campaigns/9999", json={"name": "Novo nome"}).status_code == 404
    assert api.post("/api/ask", json={
        "question": "Investigue as pistas.", "mode": "mission", "campaign_id": 9999,
    }).status_code == 404
    assert api.post("/api/campaigns", json={"name": "  "}).status_code == 422
    assert api.post("/api/ask", json={"question": "   "}).status_code == 422
    assert api.post("/api/ask", json={"question": "teste", "mode": "execute_code"}).status_code == 422
    assert api.get("/api/status").json()["used_calls"] == 0


def test_campaign_context_keeps_recent_events_in_order_and_warns(app_env):
    with closing(connect(app_env.settings.db_path)) as conn:
        with conn:
            campaign_id = conn.execute(
                "INSERT INTO campaigns(name,summary,house_rules) VALUES (?,?,?)",
                ("Campanha", "Resumo estável", "Regra local"),
            ).lastrowid
            for number in range(15):
                conn.execute(
                    "INSERT INTO campaign_events(campaign_id,text) VALUES (?,?)",
                    (campaign_id, f"Marco {number:02}: " + "x" * 1900),
                )
        context, warnings = campaign_context(conn, campaign_id)
    assert "Resumo estável" in context and "Regra local" in context
    assert "Marco 11:" not in context
    assert "Marco 12:" in context
    assert context.index("Marco 12:") < context.index("Marco 13:") < context.index("Marco 14:")
    assert warnings and "recentes" in warnings[0]


@pytest.mark.parametrize("mode,text,expected_status,citation_status", [
    ("consult", "Informação do livro: investigue pistas [1].", "answered", "present"),
    ("consult", "Informação do livro: consulte [99].", "needs_review", "invalid"),
    ("consult", "Os trechos não bastam para responder.", "needs_review", "absent"),
    ("mission", "Sugestão criativa: uma visita ao farol.", "answered", "absent"),
])
def test_modes_citation_checks_and_audit_metadata(
    app_env, monkeypatch, mode, text, expected_status, citation_status,
):
    prompts = []

    def generate(settings, instructions, prompt, client=None):
        prompts.append((instructions, prompt))
        return Generation(text, 30, 8, "stop")

    monkeypatch.setattr(answer_module, "generate_reply", generate)
    question = "Como investigar pistas? private-question-marker"
    result = app_env.client.post("/api/ask", json={"question": question, "mode": mode})
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == expected_status
    assert data["citation_check"] == citation_status
    if mode == "consult":
        assert "Use exclusivamente os trechos numerados" in prompts[0][0]
    else:
        assert "Sugestão criativa" in prompts[0][0]
    if expected_status == "needs_review":
        assert data["warnings"]
    with closing(connect(app_env.settings.db_path)) as conn:
        call = dict(conn.execute("SELECT * FROM llm_calls").fetchone())
        context = dict(conn.execute("SELECT * FROM llm_call_context").fetchone())
        book_hash = conn.execute("SELECT sha256 FROM books").fetchone()[0]
    assert call["input_tokens"] == 30 and call["output_tokens"] == 8
    assert context["call_id"] == call["id"]
    assert context["mode"] == mode
    assert context["citation_status"] == citation_status
    assert context["book_sha256"] == book_hash
    assert context["prompt_version"]
    assert json.loads(context["source_ids"]) == [s["id"] for s in data["sources"]]
    assert context["total_ms"] >= 0 and context["retrieval_ms"] >= 0
    assert "private-question-marker" not in json.dumps([context, call])
    assert "test-key-only" not in json.dumps([context, call, data])


def test_unexpected_generation_error_is_redacted_and_attempt_recorded(app_env, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private-provider-response-with-key")

    monkeypatch.setattr(answer_module, "generate_reply", fail)
    result = app_env.client.post("/api/ask", json={"question": "Como investigar pistas?"})
    assert result.status_code == 502
    assert "private-provider-response-with-key" not in result.text
    with closing(connect(app_env.settings.db_path)) as conn:
        assert conn.execute("SELECT status FROM llm_calls").fetchone()[0] == "error"
        assert conn.execute("SELECT citation_status FROM llm_call_context").fetchone()[0] == "not_generated"


def test_daily_quota_is_atomic_across_two_connections(app_env):
    barrier = Barrier(2)

    def reserve_once():
        with closing(connect(app_env.settings.db_path)) as conn:
            barrier.wait(timeout=5)
            call_id = reserve_call(conn, "test-model", 1)
            # Reservar não pode manter a transação durante a chamada remota.
            assert not conn.in_transaction
            return call_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve_once) for _ in range(2)]
        ids = [future.result(timeout=10) for future in futures]
    assert sum(value is not None for value in ids) == 1
    with closing(connect(app_env.settings.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM llm_calls").fetchone()[0] == "pending"
        assert reserve_call(conn, "another-model", 1) is None
    assert app_env.client.get("/api/status").json()["used_calls"] == 1


def test_reserved_attempt_blocks_request_without_network(app_env, monkeypatch):
    settings = replace(app_env.settings, max_calls_per_day=1)
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)
    with closing(connect(settings.db_path)) as conn:
        assert reserve_call(conn, "test-model", 1) is not None
    result = app_env.client.post("/api/ask", json={"question": "Investigar pistas"})
    assert result.status_code == 200
    assert result.json()["status"] == "daily_limit"
    assert result.json()["sources"]


@pytest.mark.parametrize("endpoint", ["/api/status", "/api/ask"])
def test_request_keeps_database_and_options_from_same_configuration(app_env, monkeypatch, endpoint):
    # O .env pode ser editado com o servidor aberto. Uma mudança de caminho
    # deve valer na próxima requisição, sem trocar de livro no meio da atual.
    current = replace(app_env.settings, groq_api_key="")
    upcoming = replace(current, db_path=current.db_path.with_name("other.sqlite3"))
    configurations = iter([current])
    monkeypatch.setattr(api_module, "get_settings", lambda: next(configurations, upcoming))

    if endpoint == "/api/status":
        result = app_env.client.get(endpoint)
        assert result.status_code == 200
        assert result.json()["book"]["title"] == "Livro demonstrativo autorizado"
    else:
        result = app_env.client.post(endpoint, json={"question": "Como investigar pistas?"})
        assert result.status_code == 200
        assert result.json()["sources"][0]["title"] == "Livro demonstrativo autorizado"

    # A alteração não é ignorada: a requisição seguinte já usa o novo banco.
    assert app_env.client.get("/api/status").json()["book"] is None


@pytest.mark.parametrize("headers,expected", [
    ({"origin": "https://untrusted.example"}, 403),
    ({"origin": "null"}, 403),
    ({"sec-fetch-site": "cross-site"}, 403),
    ({"content-type": "text/plain"}, 415),
    ({"host": "untrusted.example"}, 400),
    ({"origin": "http://[bad"}, 403),
])
def test_local_service_rejects_foreign_or_malformed_requests(app_env, headers, expected):
    result = app_env.client.post("/api/campaigns", json={"name": "Não deve salvar"}, headers=headers)
    assert result.status_code == expected
    assert app_env.client.get("/api/campaigns").json() == []


def test_request_limit_counts_actual_bytes_even_when_content_length_lies(app_env):
    result = app_env.client.post(
        "/api/campaigns",
        content=json.dumps({"name": "large", "summary": "x" * 33000}),
        headers={"content-type": "application/json", "content-length": "1"},
    )
    assert result.status_code == 413
    assert app_env.client.get("/api/campaigns").json() == []


def test_same_origin_requests_work_and_api_responses_have_protective_headers(app_env):
    result = app_env.client.post(
        "/api/campaigns", json={"name": "Permitida"},
        headers={"origin": "http://localhost", "sec-fetch-site": "same-origin"},
    )
    assert result.status_code == 201
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in result.headers["content-security-policy"]
    status = app_env.client.get("/api/status")
    assert "test-key-only" not in status.text
    assert str(app_env.settings.db_path) not in status.text
    assert status.json()["llm_configured"] is True


def test_page_rendering_checks_original_hash_and_never_exposes_file_path(app_env):
    api = app_env.client
    sources = api.post("/api/search", json={"question": "investigacao pistas"}).json()["sources"]
    assert sources
    chunk_id = sources[0]["id"]
    public_source = api.get(f"/api/sources/{chunk_id}")
    assert public_source.status_code == 200
    assert "pdf_path" not in public_source.json()
    assert "sha256" not in public_source.json()
    assert str(app_env.pdf_path) not in public_source.text
    for endpoint in (f"/api/sources/{chunk_id}/page.png", "/api/book/pages/1/image"):
        result = api.get(endpoint)
        assert result.status_code == 200
        assert result.content.startswith(b"\x89PNG")
        assert result.headers["cache-control"] == "no-store"
    assert api.get("/api/book/pages/0/image").status_code == 404
    assert api.get("/api/book/pages/999/image").status_code == 404

    # Continua sendo um PDF válido de uma página. Só conferir a quantidade de
    # páginas não detectaria esta substituição do documento indexado.
    app_env.pdf_path.write_bytes(app_env.pdf_path.read_bytes() + b"\n% modified after import\n")
    for endpoint in (f"/api/sources/{chunk_id}/page.png", "/api/book/pages/1/image"):
        result = api.get(endpoint)
        assert result.status_code == 409
        assert "mudou" in result.json()["detail"]
    app_env.pdf_path.unlink()
    assert api.get(f"/api/sources/{chunk_id}/page.png").status_code == 404


def test_search_sources_cannot_open_same_page_in_a_replacement_book(app_env):
    result = app_env.client.post("/api/search", json={"question": "investigacao pistas"}).json()
    assert result["sources"]
    old_hash = result["book_sha256"]
    page = result["sources"][0]["pdf_page"]
    endpoint = f"/api/book/pages/{page}/image?book_sha256={old_hash}"
    assert app_env.client.get(endpoint).status_code == 200

    replacement = app_env.pdf_path.with_name("replacement.pdf")
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 70), "Outro livro: investigacao de pistas.")
        document.save(replacement)
    import_pdf(replacement, app_env.settings.db_path, "Outro livro", "2")
    assert app_env.client.get(endpoint).status_code == 409
