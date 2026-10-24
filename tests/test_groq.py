"""Testa o SDK real com transporte HTTP local; nenhuma chamada externa é feita."""
from dataclasses import replace
import json
from pathlib import Path

from fastapi.testclient import TestClient
import groq
import httpx
import pytest

import rpg_assistente.api as api_module
import rpg_assistente.config as config_module
import rpg_assistente.llm as llm_module
from rpg_assistente.answer import answer_question
from rpg_assistente.config import PROJECT_ROOT, Settings
from rpg_assistente.db import connect
from rpg_assistente.ingest import import_pdf


@pytest.fixture
def book(tmp_path):
    db_path = tmp_path / "demo.sqlite3"
    import_pdf(PROJECT_ROOT / "examples/demo_rules.pdf", db_path, "Crônicas do Farol", "0.1")
    settings = Settings(
        db_path=db_path, groq_api_key="test-key", groq_model="openai/gpt-oss-120b",
        max_calls_per_day=2, max_output_tokens=2048, timeout_seconds=10,
        input_usd_per_million=None, output_usd_per_million=None,
        allow_private_book_to_groq=False, vector_path=tmp_path / "chroma",
        consult_tools_enabled=False,
    )
    conn = connect(db_path)
    try:
        yield conn, settings
    finally:
        conn.close()


def completion(text="Informação do livro: role 1d20 e some a perícia [1].",
               finish_reason="stop", with_usage=True):
    result = {
        "id": "test", "object": "chat.completion", "created": 0,
        "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": text,
                                 "reasoning": "Não deve aparecer na interface."}}],
    }
    if with_usage:
        result["usage"] = {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75,
                           "completion_tokens_details": {"reasoning_tokens": 5}}
    return result


def test_groq_wire_request_response_and_usage(book):
    conn, settings = book
    settings = replace(settings, input_usd_per_million=0.15, output_usd_per_million=0.60)

    def handler(request):
        assert request.url == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-oss-120b"
        assert body["max_completion_tokens"] == 2048
        assert body["reasoning_effort"] == "low"
        assert body["include_reasoning"] is False
        assert "reasoning_format" not in body
        assert body["messages"][0]["role"] == "system"
        prompt = body["messages"][1]["content"]
        assert "teste de perícia" in prompt
        assert "[1] Crônicas do Farol (0.1), PDF p. 1" in prompt
        return httpx.Response(200, json=completion())

    with groq.Groq(api_key="test-key", max_retries=0,
                   http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        result = answer_question(conn, "teste de perícia", settings, client)
    assert result["status"] == "answered"
    assert "Não deve aparecer" not in result["answer"]
    assert result["sources"][0]["pdf_page"] == 1
    assert result["usage"]["provider"] == "groq"
    assert result["usage"]["input_tokens"] == 50
    assert result["usage"]["output_tokens"] == 25  # Inclui os 5 de raciocínio.
    assert result["usage"]["estimated_usd"] == pytest.approx(0.0000225)
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["model"] == "openai/gpt-oss-120b"
    assert row["status"] == "ok"
    assert row["elapsed_ms"] >= 0


@pytest.mark.parametrize("code, expected", [
    (400, "solicitação"), (401, "GROQ_API_KEY"), (403, "permissão"),
    (404, "GROQ_MODEL"), (413, "tamanho"), (422, "parâmetros"),
    (429, "limite"), (503, "indisponível"),
])
def test_http_errors_preserve_sources_and_hide_response_body(book, monkeypatch, code, expected):
    conn, settings = book
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(code, json={"error": {"message": "sensitive-key-and-book"}})

    real_client = groq.Groq

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"] == settings.timeout_seconds
        return real_client(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(llm_module.groq, "Groq", factory)
    result = answer_question(conn, "teste de perícia", settings)
    assert len(requests) == 1
    assert result["status"] == "api_error"
    assert result["error_code"] == code
    assert expected in result["answer"]
    assert "sensitive-key-and-book" not in json.dumps(result)
    assert result["sources"][0]["pdf_page"] == 1
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["status"] == f"error_{code}"
    assert row["input_tokens"] is None


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_network_errors_preserve_sources(book, error):
    conn, settings = book

    def handler(request):
        raise error("sensitive-network-details", request=request)

    with groq.Groq(api_key="test-key", max_retries=0,
                   http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        result = answer_question(conn, "teste de perícia", settings, client)
    assert result["status"] == "api_error"
    assert result["sources"]
    assert "sensitive-network-details" not in result["answer"]
    assert conn.execute("SELECT status FROM llm_calls").fetchone()[0] == "error"


@pytest.mark.parametrize("text, reason, status", [
    ("Resposta parcial", "length", "incomplete_response"),
    ("", "length", "incomplete_response"),
    (None, "stop", "api_error"),
    ("", "stop", "api_error"),
    (None, "content_filter", "incomplete_response"),
])
def test_empty_or_incomplete_answer_is_not_presented_as_success(book, text, reason, status):
    conn, settings = book
    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=completion(text, reason)))
    )) as client:
        result = answer_question(conn, "teste de perícia", settings, client)
    assert result["status"] == status
    assert "Resposta parcial" not in result["answer"]
    assert result["sources"]
    assert result["usage"]["output_tokens"] == 25
    assert conn.execute("SELECT status FROM llm_calls").fetchone()[0] != "ok"


def test_missing_usage_is_unknown_and_daily_limit_stops_second_request(book):
    conn, settings = book
    settings = replace(settings, max_calls_per_day=1)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=completion(with_usage=False))

    with groq.Groq(api_key="test-key", max_retries=0,
                   http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        first = answer_question(conn, "teste de perícia", settings, client)
        second = answer_question(conn, "teste de perícia", settings, client)
    assert first["status"] == "answered"
    assert first["usage"]["input_tokens"] is None
    assert first["usage"]["output_tokens"] is None
    assert first["usage"]["estimated_usd"] is None
    assert second["status"] == "daily_limit"
    assert len(requests) == 1


def test_key_and_private_book_guards_do_not_call_provider(book, monkeypatch):
    conn, settings = book

    def fail(**kwargs):
        pytest.fail("Não deveria criar cliente sem chave ou permissão de envio.")

    monkeypatch.setattr(llm_module.groq, "Groq", fail)
    result = answer_question(conn, "teste de perícia", replace(settings, groq_api_key=""))
    assert result["status"] == "sources_only"
    assert "GROQ_API_KEY" in result["answer"]
    conn.execute("UPDATE books SET pdf_path = ?", (str(settings.db_path.parent / "private.pdf"),))
    result = answer_question(conn, "teste de perícia", settings)
    assert result["status"] == "private_book_local_only"
    assert "ALLOW_PRIVATE_BOOK_TO_GROQ" in result["answer"]
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0


def test_fastapi_returns_groq_answer_with_sources(book, monkeypatch):
    conn, settings = book
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)
    real_client = groq.Groq

    def factory(**kwargs):
        return real_client(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=completion())
        )))

    monkeypatch.setattr(llm_module.groq, "Groq", factory)
    with TestClient(api_module.app, base_url="http://localhost") as api:
        response = api.post("/api/ask", json={"question": "teste de perícia"})
        assert response.status_code == 200
        assert response.json()["status"] == "answered"
        assert response.json()["usage"]["model"] == "openai/gpt-oss-120b"
        chunk_id = response.json()["sources"][0]["id"]
        assert api.get(f"/api/sources/{chunk_id}/page.png").headers["content-type"] == "image/png"


def test_config_ignores_legacy_gemini_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    for key in ("GROQ_API_KEY", "GROQ_MODEL", "GROQ_TIMEOUT_SECONDS", "GROQ_REASONING_EFFORT",
                "ALLOW_PRIVATE_BOOK_TO_GROQ", "GROQ_INPUT_USD_PER_MILLION", "GROQ_OUTPUT_USD_PER_MILLION"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "GEMINI_API_KEY=legacy-key\nGEMINI_MODEL=legacy-model\n"
        "ALLOW_PRIVATE_BOOK_TO_GEMINI=true\nINPUT_USD_PER_MILLION=123\n", encoding="utf-8",
    )
    settings = config_module.get_settings()
    assert settings.groq_api_key == ""
    assert settings.groq_model == "openai/gpt-oss-120b"
    assert settings.allow_private_book_to_groq is False
    assert settings.input_usd_per_million is None
    assert settings.reasoning_effort == "low"
    assert "test-key" not in repr(replace(settings, groq_api_key="test-key"))
