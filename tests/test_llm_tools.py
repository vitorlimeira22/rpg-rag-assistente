"""Contrato Groq de function calling com SDK real e transporte sem rede."""

from copy import deepcopy
from dataclasses import replace
import json

import groq
import httpx
import pytest

from rpg_assistente.config import Settings
from rpg_assistente.llm import Generation, GenerationError, ToolCall, generate_reply, generate_turn


TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_book", "description": "Busca evidências no livro importado.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}]


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=tmp_path / "unused.sqlite3", groq_api_key="test-key",
        groq_model="openai/gpt-oss-120b", max_calls_per_day=3,
        max_output_tokens=2048, timeout_seconds=10,
        input_usd_per_million=None, output_usd_per_million=None,
        allow_private_book_to_groq=False, vector_path=tmp_path / "unused-chroma",
    )


def completion(*, text=None, tool_calls=None, finish_reason="tool_calls", usage=True):
    message = {
        "role": "assistant", "content": text,
        "reasoning": "Raciocínio interno que não deve aparecer no resultado público.",
    }
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    result = {
        "id": "chat-test", "object": "chat.completion", "created": 0,
        "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
    }
    if usage:
        result["usage"] = {"prompt_tokens": 40, "completion_tokens": 15, "total_tokens": 55}
    return result


def tool_call(call_id="call-1", name="search_book", arguments='{"query":"rituais"}'):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_generate_turn_sends_tools_and_normalizes_requests_without_executing(settings):
    requests = []
    messages = [{"role": "system", "content": "Use evidências."},
                {"role": "user", "content": "Compare duas regras."}]
    original_messages = deepcopy(messages)
    raw_calls = [tool_call(), tool_call("call-2", arguments='{"query":"custos"}')]

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["tools"] == TOOLS
        assert body["tool_choice"] == "auto"
        assert body["messages"] == original_messages
        assert body["max_completion_tokens"] == 321
        assert body["reasoning_effort"] == "low"
        assert body["include_reasoning"] is False
        return httpx.Response(200, json=completion(tool_calls=raw_calls))

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        turn = generate_turn(settings, messages, tools=TOOLS, client=client, max_output_tokens=321)
        assert not client.is_closed()
    assert len(requests) == 1
    assert messages == original_messages
    assert turn.tool_calls == [
        ToolCall("call-1", "search_book", '{"query":"rituais"}'),
        ToolCall("call-2", "search_book", '{"query":"custos"}'),
    ]
    assert turn.text == ""
    assert turn.finish_reason == "tool_calls"
    assert turn.input_tokens == 40 and turn.output_tokens == 15
    assert "Raciocínio interno" not in repr(turn)
    assert not settings.db_path.exists()  # O adaptador não executou a busca.


def test_tool_results_round_trip_with_matching_roles_and_ids(settings):
    call = tool_call()
    messages = [
        {"role": "system", "content": "Use evidências."},
        {"role": "user", "content": "Qual é o custo?"},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "call-1", "content": '{"sources":[{"id":7}]}'},
    ]

    def handler(request):
        body = json.loads(request.content)
        assert body["messages"] == messages
        assert body["max_completion_tokens"] == settings.max_output_tokens
        assert "tools" not in body and "tool_choice" not in body
        return httpx.Response(200, json=completion(
            text="  Informação do livro: confira a fonte [1].  ", finish_reason="stop",
        ))

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        turn = generate_turn(settings, messages, client=client)
    assert turn.finish_reason == "stop"
    assert turn.text == "Informação do livro: confira a fonte [1]."
    assert turn.tool_calls == []


def test_arguments_json_is_preserved_for_orchestrator_validation(settings):
    # O provedor pode produzir argumentos inválidos; a execução deve depender de
    # validação explícita no orquestrador, não de "consertar" ou avaliar esta string.
    raw = '{"query": invalid-json-with-private-data}'
    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion(tool_calls=[tool_call(arguments=raw)])),
        ),
    )) as client:
        turn = generate_turn(settings, [{"role": "user", "content": "Consulte o livro"}],
                             tools=TOOLS, client=client)
    assert turn.tool_calls[0].arguments == raw


@pytest.mark.parametrize("calls", [
    [],
    [tool_call(call_id="")],
    [tool_call(name="")],
    [tool_call(), tool_call()],
])
def test_malformed_tool_protocol_fails_without_exposing_payload(settings, calls):
    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion(
                text="private-provider-text", tool_calls=calls,
            )),
        ),
    )) as client:
        with pytest.raises(GenerationError) as error:
            generate_turn(settings, [{"role": "user", "content": "Consulte"}], tools=TOOLS, client=client)
    assert "ferramenta" in str(error.value)
    assert "private-provider-text" not in str(error.value)
    assert "test-key" not in str(error.value)


def test_legacy_reply_and_non_gpt_model_keep_compatible_wire_format(settings):
    settings = replace(settings, groq_model="another-model")

    def handler(request):
        body = json.loads(request.content)
        assert body["messages"] == [
            {"role": "system", "content": "Instruções"},
            {"role": "user", "content": "Pergunta"},
        ]
        assert "reasoning_effort" not in body and "include_reasoning" not in body
        assert "tools" not in body and "tool_choice" not in body
        return httpx.Response(200, json=completion(text="Resposta", finish_reason="stop", usage=False))

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        result = generate_reply(settings, "Instruções", "Pergunta", client)
    assert type(result) is Generation
    assert result == Generation("Resposta", None, None, "stop")


def test_qwen_tools_use_hidden_reasoning_format_and_requested_effort(settings):
    settings = replace(settings, groq_model="qwen/qwen3.8-27b", reasoning_effort="medium")
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "qwen/qwen3.8-27b"
        assert body["reasoning_effort"] == "medium"
        assert body["reasoning_format"] == "hidden"
        assert "include_reasoning" not in body
        assert body["tools"] == TOOLS and body["tool_choice"] == "auto"
        response = completion(tool_calls=[tool_call()])
        response["model"] = settings.groq_model
        return httpx.Response(200, json=response)

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        turn = generate_turn(
            settings, [{"role": "user", "content": "Compare duas opções do livro."}],
            tools=TOOLS, client=client,
        )
    assert len(requests) == 1
    assert turn.tool_calls == [ToolCall("call-1", "search_book", '{"query":"rituais"}')]
    assert turn.text == ""
    assert "Raciocínio interno" not in repr(turn)


@pytest.mark.parametrize("status", [400, 429, 503])
def test_turn_errors_use_redacted_shared_provider_mapping(settings, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={
            "error": {"message": "private-provider-body test-key Authorization: secret"},
        }, headers={"x-private-header": "private-provider-header"})

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        with pytest.raises(GenerationError) as error:
            generate_turn(settings, [{"role": "user", "content": "Pedido privado"}],
                          tools=TOOLS, client=client)
    assert error.value.code == status
    assert len(requests) == 1
    assert "private-provider" not in str(error.value)
    assert "test-key" not in str(error.value)
    assert "Authorization" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_turn_network_errors_are_redacted(settings, error_type):
    def handler(request):
        raise error_type("private-connection-details", request=request)

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        with pytest.raises(GenerationError) as error:
            generate_turn(settings, [{"role": "user", "content": "Pedido privado"}],
                          tools=TOOLS, client=client)
    assert "private-connection-details" not in str(error.value)
    assert error.value.code is None


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_per_turn_limit_fails_before_any_provider_call(settings, limit):
    def handler(request):
        pytest.fail("Configuração inválida não pode consumir uma chamada.")

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        with pytest.raises(ValueError, match="inteiro positivo"):
            generate_turn(settings, [{"role": "user", "content": "Consulte"}],
                          client=client, max_output_tokens=limit)


@pytest.mark.parametrize("duration,expected_seconds", [
    ("0s", 0.0),
    ("7.66s", 7.66),
    ("2m59.56s", 179.56),
    ("1440m0s", 86400.0),
])
def test_rate_headers_return_only_normalized_token_metadata(settings, duration, expected_seconds):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json=completion(text="Resposta [1].", finish_reason="stop"),
            headers={
                "x-ratelimit-remaining-tokens": "0",
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-reset-tokens": duration,
                "x-ratelimit-remaining-requests": "999",
                "x-private-header": "private-header-must-not-leave-adapter",
            },
        )

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        turn = generate_turn(settings, [{"role": "user", "content": "Consulte"}], client=client)
        assert not client.is_closed()
    assert len(requests) == 1
    assert turn.rate_remaining_tokens == 0
    assert turn.rate_limit_tokens == 8000
    assert turn.rate_reset_seconds == pytest.approx(expected_seconds)
    assert "private-header" not in repr(turn)
    assert "999" not in repr(turn)
    assert turn.text == "Resposta [1]."


@pytest.mark.parametrize("integer,duration", [
    ("-1", "-1s"),
    ("+1", "+1s"),
    ("1.5", "1.5m"),
    ("1e3", "1e3s"),
    ("NaN", "NaNs"),
    ("Infinity", "infs"),
    (" 2 ", " 2s "),
    ("", ""),
    ("20, 30", "2m59s, 3s"),
    ("unknown", "2m"),
    ("unknown", "1h0m0s"),
    ("unknown", "86400.01s"),
    ("unknown", "1441m0s"),
    ("9" * 5000, "9" * 5000 + "s"),
])
def test_invalid_rate_headers_remain_unknown_without_exposing_raw_values(settings, integer, duration):
    def handler(request):
        return httpx.Response(
            200, json=completion(text="Resposta.", finish_reason="stop"),
            headers={
                "x-ratelimit-remaining-tokens": integer,
                "x-ratelimit-limit-tokens": integer,
                "x-ratelimit-reset-tokens": duration,
            },
        )

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        turn = generate_turn(settings, [{"role": "user", "content": "Consulte"}], client=client)
    assert turn.rate_remaining_tokens is None
    assert turn.rate_limit_tokens is None
    assert turn.rate_reset_seconds is None
    assert turn.text == "Resposta."


def test_missing_rate_header_does_not_erase_other_valid_fields(settings):
    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json=completion(tool_calls=[tool_call()]),
            headers={"x-ratelimit-remaining-tokens": "321", "x-ratelimit-limit-tokens": "bad"},
        )),
    )) as client:
        turn = generate_turn(
            settings, [{"role": "user", "content": "Consulte"}], tools=TOOLS, client=client,
        )
    assert turn.rate_remaining_tokens == 321
    assert turn.rate_limit_tokens is None
    assert turn.rate_reset_seconds is None
    assert turn.tool_calls == [ToolCall("call-1", "search_book", '{"query":"rituais"}')]


def test_required_tool_choice_is_serialized_to_groq(settings):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["tool_choice"] == "required"
        assert body["tools"] == TOOLS
        return httpx.Response(200, json=completion(tool_calls=[tool_call()]))

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        turn = generate_turn(
            settings, [{"role": "user", "content": "Pesquise antes de comparar."}],
            tools=TOOLS, tool_choice="required", client=client,
        )
    assert len(requests) == 1
    assert turn.tool_calls == [ToolCall("call-1", "search_book", '{"query":"rituais"}')]


@pytest.mark.parametrize("choice,tools", [
    ("required", None),
    ("required", []),
    ("none", TOOLS),
    ("invalid", TOOLS),
    (None, TOOLS),
    ({"type": "function"}, TOOLS),
])
def test_invalid_tool_choice_fails_before_provider_request(settings, choice, tools):
    def handler(request):
        pytest.fail("Escolha de ferramenta inválida não pode gerar tráfego.")

    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler),
    )) as client:
        with pytest.raises(ValueError, match="tool_choice"):
            generate_turn(
                settings, [{"role": "user", "content": "Consulte"}],
                tools=tools, tool_choice=choice, client=client,
            )


def test_alternative_citation_formats_are_normalized_after_sdk_response(settings):
    with groq.Groq(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json=completion(
                text="  Efeito 【7】, restrição 【٨†】. Veja 【Livro】.  ",
                finish_reason="stop",
            ),
        )),
    )) as client:
        turn = generate_turn(settings, [{"role": "user", "content": "Consulte"}], client=client)
    assert turn.text == "Efeito [7], restrição [8]. Veja 【Livro】."
