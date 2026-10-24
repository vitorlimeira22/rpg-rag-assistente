"""Pesquisa em etapas com PDF sintético, SQLite real e LLM simulada."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pymupdf
import pytest

import rpg_assistente.answer as answer_module
import rpg_assistente.research as research_module
import rpg_assistente.vector as vector_module
from rpg_assistente.book_tools import BookTools
from rpg_assistente.config import Settings
from rpg_assistente.db import connect
from rpg_assistente.ingest import import_pdf
from rpg_assistente.llm import GenerationError, GenerationTurn, ToolCall
from rpg_assistente.search import source


@pytest.fixture
def research_env(tmp_path, monkeypatch):
    pdf_path = tmp_path / "authorized-fiction.pdf"
    texts = [
        "Escudo de nevoa protege uma pessoa. O efeito termina quando ela se move.",
        "Fulgor ilumina uma sala. Custa dois pontos e dura uma cena.",
        "Vinculo exige concentracao continua. Ele termina se o alvo ficar distante.",
    ]
    with pymupdf.open() as document:
        for text in texts:
            page = document.new_page()
            page.insert_textbox(pymupdf.Rect(40, 50, 500, 200), text)
        document.set_toc([[1, "Efeitos demonstrativos", 1], [2, "Fulgor", 2]])
        document.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 11}])
        document.save(pdf_path)
    settings = Settings(
        db_path=tmp_path / "local.sqlite3",
        vector_path=tmp_path / "chroma",
        groq_api_key="test-key-never-sent",
        groq_model="test-model",
        max_calls_per_day=10,
        max_output_tokens=1500,
        timeout_seconds=10,
        input_usd_per_million=1.0,
        output_usd_per_million=2.0,
        allow_private_book_to_groq=True,
        consult_tools_enabled=True,
        consult_max_model_calls=3,
        consult_max_tool_calls=4,
    )
    import_pdf(pdf_path, settings.db_path, "Livro ficticio autorizado", "1")
    conn = connect(settings.db_path)
    initial_source = source(conn, 1)
    initial_source["methods"] = ["textual"]
    initial = {
        "sources": [initial_source],
        "warnings": [],
        "retrieval": {"elapsed_ms": 2, "rerank_status": "disabled"},
    }
    retrieval_calls = []

    def initial_retrieve(connection, question, config, limit):
        retrieval_calls.append((question, limit))
        assert connection is conn
        return deepcopy(initial)

    def unexpected_generation(*args, **kwargs):
        pytest.fail("Nenhuma chamada externa ou geração não simulada é permitida.")

    monkeypatch.setattr(answer_module, "retrieve", initial_retrieve)
    monkeypatch.setattr(answer_module, "generate_reply", unexpected_generation)
    monkeypatch.setattr(research_module, "generate_turn", unexpected_generation)
    # A busca textual das ferramentas continua real; nenhum encoder é carregado.
    monkeypatch.setattr(vector_module, "vector_search", lambda *args, **kwargs: [])
    try:
        yield SimpleNamespace(
            conn=conn, settings=settings, initial=initial, texts=texts,
            retrieval_calls=retrieval_calls,
        )
    finally:
        conn.close()


def ask(env, **changes):
    return answer_module.answer_question(
        env.conn, "Compare efeitos e suas restricoes.", replace(env.settings, **changes),
    )


def tool_turn(*calls, input_tokens=100, output_tokens=20):
    return GenerationTurn("", input_tokens, output_tokens, "tool_calls", list(calls))


def final_turn(text="O efeito protege uma pessoa [1].", *, input_tokens=200, output_tokens=40):
    return GenerationTurn(text, input_tokens, output_tokens, "stop")


def read_call(call_id, *pages):
    return ToolCall(call_id, "read_book_pages", json.dumps({"pdf_pages": list(pages)}))


def install_turns(monkeypatch, env, *turns):
    captured = []
    remaining = iter(turns)

    def generate(settings, messages, **kwargs):
        # A reserva precisa estar confirmada antes de aguardar o provedor.
        assert env.conn.in_transaction is False
        captured.append({"messages": deepcopy(messages), **deepcopy(kwargs)})
        try:
            turn = next(remaining)
        except StopIteration:
            pytest.fail("O orquestrador excedeu a quantidade esperada de chamadas.")
        if isinstance(turn, Exception):
            raise turn
        return turn

    monkeypatch.setattr(research_module, "generate_turn", generate)
    return captured


def tool_payloads(captured_turn):
    return [
        json.loads(message["content"])
        for message in captured_turn["messages"] if message["role"] == "tool"
    ]


def call_rows(env):
    return env.conn.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()


def test_numeric_ranking_uses_catalog_without_top_k_or_paid_generation(research_env, monkeypatch):
    env = research_env
    catalog = {
        "coverage": {"parsed_statblocks": 49, "scanned_pages": 330,
                     "complete_for_detected_statblocks": True},
        "criterion": "VD", "ranking": "highest_per_element", "truncated": False,
        "entries": [{"name": "Sentinela", "vd": 80, "primary_element": "Sangue",
                     "source_id": env.initial["sources"][0]["id"]}],
    }
    selections = []

    def select(conn, **kwargs):
        selections.append(kwargs)
        return {"sources": env.initial["sources"], "warnings": [],
                "structured_catalog": catalog}

    monkeypatch.setattr(answer_module, "select_creatures", select)
    result = answer_module.answer_question(
        env.conn, "Quais as criaturas mais fortes por elemento?",
        replace(env.settings, groq_api_key="", allow_private_book_to_groq=False),
        history=[{"role": "user", "content": "Compare usando VD."}],
    )
    assert selections == [{"element": None, "ranking": "highest_per_element", "limit": 20}]
    assert env.retrieval_calls == []
    assert result["structured_catalog"] == catalog
    assert result["usage"] is None
    assert result["research"]["model_calls"] == 0
    assert call_rows(env) == []
    assert "| Sangue | Sentinela | 80 | [1] |" in result["answer"]
    assert "330 páginas" in result["answer"]
    assert result["status"] == "answered"
    assert result["retrieval"]["strategy"] == "structured_catalog"


def test_unsupported_catalog_falls_back_to_normal_retrieval(research_env, monkeypatch):
    env = research_env
    install_turns(monkeypatch, env, final_turn("Os trechos não permitem comparar o VD [1]."))
    result = answer_module.answer_question(
        env.conn, "Quais as criaturas mais fortes por elemento?", env.settings,
    )
    assert len(env.retrieval_calls) == 1
    assert "structured_catalog" not in result


def test_two_turns_read_real_page_and_audit_each_attempt(research_env, monkeypatch):
    env = research_env
    captured = install_turns(
        monkeypatch, env, tool_turn(read_call("read-2", 2)),
        final_turn("Fulgor custa dois pontos e dura uma cena [2]."),
    )
    result = ask(env)

    assert result["status"] == "answered"
    assert result["citation_check"] == "present"
    assert env.retrieval_calls == [("Compare efeitos e suas restricoes.", 3)]
    assert [(item["citation_id"], item["pdf_page"]) for item in result["sources"]] == [(1, 1), (2, 2)]
    assert result["sources"][1]["printed_page"] == "12"
    assert result["sources"][1]["source_kind"] == "page"
    payload = tool_payloads(captured[1])[0]
    assert env.texts[1] in payload["sources"][0]["text"]
    assert payload["sources"][0]["citation_id"] == 2
    assert "pdf_path" not in json.dumps(captured)
    assert "test-key-never-sent" not in json.dumps(captured)
    assert [message["role"] for message in captured[1]["messages"]] == ["system", "user", "assistant", "tool"]
    assert captured[1]["messages"][-1]["tool_call_id"] == "read-2"
    assert {tool["function"]["name"] for tool in captured[0]["tools"]} == {"search_book", "read_book_pages"}
    assert captured[0]["tool_choice"] == "auto"
    assert captured[1]["tool_choice"] == "auto"
    assert result["research"] == {
        "model_calls": 2, "tool_calls": 1,
        "steps": [{"tool": "read_book_pages", "status": "ok"}],
    }
    assert result["usage"]["input_tokens"] == 300
    assert result["usage"]["output_tokens"] == 60
    assert result["usage"]["estimated_usd"] == pytest.approx(0.00042)
    assert result["usage"]["call_count"] == 2
    assert result["usage"]["total_ms"] >= result["usage"]["elapsed_ms"]
    assert [row["status"] for row in call_rows(env)] == ["tool_calls", "ok"]
    audits = env.conn.execute("SELECT * FROM llm_call_context ORDER BY call_id").fetchall()
    assert [json.loads(row["source_ids"]) for row in audits] == [[1], [1, 2]]
    assert [row["citation_status"] for row in audits] == ["not_generated", "present"]
    assert all(row["book_sha256"] and row["prompt_version"] and row["total_ms"] >= 0 for row in audits)
    assert "Fulgor" not in json.dumps([dict(row) for row in audits])


def test_search_then_page_keeps_chunk_and_page_citations_distinct(research_env, monkeypatch):
    env = research_env
    captured = install_turns(
        monkeypatch, env,
        tool_turn(ToolCall("find", "search_book", '{"query":"Fulgor"}')),
        tool_turn(read_call("read", 2)),
        final_turn("O trecho e a página confirmam a duração [2] [3]."),
    )
    result = ask(env)

    assert result["status"] == "answered"
    assert [(item["citation_id"], item["id"], item["source_kind"]) for item in result["sources"]] == [
        (1, 1, "chunk"), (2, 2, "chunk"), (3, 2, "page"),
    ]
    assert tool_payloads(captured[1])[0]["sources"][0]["citation_id"] == 2
    assert "[3]" in captured[2]["messages"][1]["content"]
    assert env.texts[1] in captured[2]["messages"][1]["content"]
    assert captured[2]["tools"] is None
    assert captured[2]["messages"][-1]["role"] == "user"
    assert result["research"]["model_calls"] == 3


def test_three_repetitions_preserve_cache_ids_without_repeating_text(research_env, monkeypatch):
    env = research_env
    captured = install_turns(
        monkeypatch, env,
        tool_turn(read_call("first", 2), read_call("second", 2), read_call("third", 2)),
        final_turn("A duração é uma cena [2]."),
    )
    result = ask(env)
    payloads = tool_payloads(captured[1])

    assert [payload["status"] for payload in payloads] == ["ok", "reused", "reused"]
    assert payloads[0]["sources"][0]["citation_id"] == 2
    assert [payload["previous_citation_ids"] for payload in payloads[1:]] == [[2], [2]]
    assert all(payload["sources"] == [] for payload in payloads[1:])
    assert len(result["sources"]) == 2
    assert result["research"]["tool_calls"] == 3
    assert json.dumps(captured[1]["messages"], ensure_ascii=False).count(env.texts[1]) == 1


def test_overlapping_page_requests_send_existing_citation_without_text(research_env, monkeypatch):
    env = research_env
    captured = install_turns(
        monkeypatch, env,
        tool_turn(read_call("one", 2), read_call("overlap", 2, 3)),
        final_turn("Compare duração e concentração [2] [3]."),
    )
    result = ask(env)
    second_payload = tool_payloads(captured[1])[1]
    assert second_payload["previous_citation_ids"] == [2]
    assert [item["citation_id"] for item in second_payload["sources"]] == [3]
    assert len(result["sources"]) == 3


@pytest.mark.parametrize("name,arguments", [
    ("read_file", '{"path":"secret-private-path"}'),
    ("search_book", '{"query":"Fulgor","command":"secret-private-path"}'),
    ("search_book", "not-json-secret-private-path"),
    ("search_book", '["secret-private-path"]'),
    ("search_book", '{"query":123}'),
    ("read_book_pages", '{"pdf_pages":["2"]}'),
    ("read_book_pages", '{"pdf_pages":[true]}'),
    ("read_book_pages", '{"pdf_pages":[2,3,1]}'),
    ("search_book", '{"query":"' + ("x" * 4001) + '"}'),
])
def test_invalid_tools_and_arguments_do_not_execute_reads(
    research_env, monkeypatch, name, arguments,
):
    env = research_env

    def unexpected_read(*args, **kwargs):
        pytest.fail("Argumentos inválidos chegaram à execução da ferramenta.")

    monkeypatch.setattr(BookTools, "_search", unexpected_read)
    monkeypatch.setattr(BookTools, "_read_pages", unexpected_read)
    captured = install_turns(
        monkeypatch, env, tool_turn(ToolCall("invalid", name, arguments)), final_turn(),
    )
    result = ask(env)
    payload = tool_payloads(captured[1])[0]
    assert payload["status"] in {"invalid", "invalid_arguments"}
    assert payload["sources"] == []
    assert result["research"]["tool_calls"] == 1
    assert len(result["sources"]) == 1
    assert result["research"]["steps"][0]["tool"] == ("invalid" if name == "read_file" else name)
    assert "secret-private-path" not in json.dumps(payload)
    assert "secret-private-path" not in json.dumps([dict(row) for row in call_rows(env)])


def test_tool_budget_replies_to_all_requests_but_only_executes_allowed_count(research_env, monkeypatch):
    env = research_env
    captured = install_turns(
        monkeypatch, env,
        tool_turn(read_call("one", 2), read_call("two", 3), read_call("three", 1), read_call("four", 2, 3)),
        final_turn("Há evidências para comparar [1] [2] [3]."),
    )
    result = ask(env, consult_max_tool_calls=3)
    assert [step["status"] for step in result["research"]["steps"]] == ["ok", "ok", "ok", "limit"]
    assert tool_payloads(captured[1]) == []
    assert captured[1]["tools"] is None
    assert result["research"]["tool_calls"] == 3
    assert len(result["sources"]) == 4
    assert result["usage"]["call_count"] == 2


@pytest.mark.parametrize("limits", [
    {"consult_max_model_calls": 2},
    {"consult_max_tool_calls": 1},
])
def test_final_synthesis_has_evidence_without_research_orders(research_env, monkeypatch, limits):
    env = research_env
    captured = install_turns(
        monkeypatch, env, tool_turn(read_call("read", 2)),
        final_turn("A luz dura uma cena [2]."),
    )
    result = ask(env, **limits)
    final = captured[-1]
    assert final["tools"] is None
    assert [message["role"] for message in final["messages"]] == ["system", "user"]
    assert research_module.RESEARCH_INSTRUCTIONS not in final["messages"][0]["content"]
    assert answer_module.INSTRUCTIONS in final["messages"][0]["content"]
    for source in result["sources"]:
        assert f"[{source['citation_id']}]" in final["messages"][1]["content"]
        assert source["text"] in final["messages"][1]["content"]
    assert "Compare efeitos e suas restricoes." in final["messages"][1]["content"]
    assert result["research"]["model_calls"] == 2


def test_model_call_limit_rejects_tools_when_final_synthesis_is_required(research_env, monkeypatch):
    env = research_env
    captured = install_turns(
        monkeypatch, env,
        tool_turn(read_call("one", 2)),
        tool_turn(read_call("two", 3)),
        tool_turn(read_call("unexpected", 1)),
    )
    result = ask(env)
    assert len(captured) == 3
    assert captured[-1]["tools"] is None
    assert result["status"] == "api_error"
    assert result["research"]["model_calls"] == 3
    assert result["research"]["tool_calls"] == 2
    assert len(result["sources"]) == 3
    assert [row["status"] for row in call_rows(env)] == ["tool_calls", "tool_calls", "unexpected_tools"]


def test_quota_exhausted_between_turns_preserves_new_sources_and_usage(research_env, monkeypatch):
    env = research_env
    captured = install_turns(monkeypatch, env, tool_turn(read_call("one", 2)))
    result = ask(env, max_calls_per_day=1)
    assert result["status"] == "daily_limit"
    assert len(captured) == 1
    assert [item["pdf_page"] for item in result["sources"]] == [1, 2]
    assert result["usage"]["call_count"] == 1
    assert result["usage"]["input_tokens"] == 100
    assert result["usage"]["output_tokens"] == 20
    assert len(call_rows(env)) == 1


def test_exhausted_quota_before_first_turn_does_not_call_provider(research_env):
    result = ask(research_env, max_calls_per_day=0)
    assert result["status"] == "daily_limit"
    assert result["usage"] is None
    assert result["research"]["model_calls"] == 0
    assert len(result["sources"]) == 1
    assert call_rows(research_env) == []


@pytest.mark.parametrize("changes,expected", [
    ({"groq_api_key": ""}, "sources_only"),
    ({"groq_model": ""}, "sources_only"),
    ({"allow_private_book_to_groq": False}, "private_book_local_only"),
])
def test_key_model_and_book_permission_are_checked_before_research(research_env, changes, expected):
    result = ask(research_env, **changes)
    assert result["status"] == expected
    assert result["usage"] is None
    assert len(result["sources"]) == 1
    assert call_rows(research_env) == []


def test_no_initial_evidence_does_not_start_provider(research_env):
    research_env.initial["sources"] = []
    result = ask(research_env)
    assert result["status"] == "insufficient_sources"
    assert result["sources"] == []
    assert call_rows(research_env) == []


def test_provider_error_after_tool_turn_keeps_sources_and_marks_unknown_total(research_env, monkeypatch):
    env = research_env
    captured = install_turns(
        monkeypatch, env, tool_turn(read_call("one", 2)),
        GenerationError("O provedor recusou esta tentativa.", code=429),
    )
    result = ask(env)
    assert len(captured) == 2
    assert result["status"] == "api_error"
    assert [item["pdf_page"] for item in result["sources"]] == [1, 2]
    assert result["usage"]["call_count"] == 2
    assert result["usage"]["input_tokens"] is None
    assert result["usage"]["output_tokens"] is None
    assert result["usage"]["estimated_usd"] is None
    rows = call_rows(env)
    assert [row["status"] for row in rows] == ["tool_calls", "error_429"]
    assert rows[0]["input_tokens"] == 100
    assert rows[1]["input_tokens"] is None
    assert all(row["status"] != "pending" for row in rows)


def test_missing_usage_stays_unknown_per_component_instead_of_zero(research_env, monkeypatch):
    env = research_env
    install_turns(
        monkeypatch, env, tool_turn(read_call("one", 2), input_tokens=None),
        final_turn(input_tokens=200, output_tokens=40),
    )
    result = ask(env)
    assert result["usage"]["input_tokens"] is None
    assert result["usage"]["output_tokens"] == 60
    assert result["usage"]["estimated_usd"] is None
    assert [row["input_tokens"] for row in call_rows(env)] == [None, 200]


@pytest.mark.parametrize("text,expected", [
    ("A fonte existe [1], mas esta outra não [999].", "invalid"),
    ("Resposta sem referência verificável.", "absent"),
])
def test_missing_or_invalid_citations_require_review_and_are_audited(research_env, monkeypatch, text, expected):
    env = research_env
    install_turns(monkeypatch, env, final_turn(text))
    result = ask(env)
    assert result["status"] == "needs_review"
    assert result["citation_check"] == expected
    assert result["warnings"]
    assert env.conn.execute("SELECT citation_status FROM llm_call_context").fetchone()[0] == expected


def test_incomplete_final_answer_is_not_presented_as_complete(research_env, monkeypatch):
    env = research_env
    install_turns(
        monkeypatch, env, tool_turn(read_call("one", 2)),
        GenerationTurn("Texto parcial que não deve aparecer [2]", 200, 100, "length"),
    )
    result = ask(env)
    assert result["status"] == "incomplete_response"
    assert "Texto parcial" not in result["answer"]
    assert len(result["sources"]) == 2
    assert result["usage"]["call_count"] == 2
    assert [row["status"] for row in call_rows(env)] == ["tool_calls", "incomplete"]


def test_rate_headers_delay_next_call_before_reserving_quota(research_env, monkeypatch):
    env = research_env
    first = replace(tool_turn(read_call("one", 2)),
                    rate_remaining_tokens=1, rate_reset_seconds=5, rate_limit_tokens=8000)
    install_turns(monkeypatch, env, first, final_turn())
    clock = [100.0]
    monkeypatch.setattr(answer_module, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(research_module, "perf_counter", lambda: clock[0])
    sleeps = []

    def wait(seconds):
        assert len(call_rows(env)) == 1  # Não reserva antes de aguardar a API.
        assert env.conn.in_transaction is False
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(research_module, "sleep", wait)
    result = ask(env)
    assert sleeps == [5.25]
    assert result["status"] == "answered"
    assert result["usage"]["rate_wait_ms"] == 5250
    assert result["usage"]["call_count"] == 2


def test_sufficient_rate_budget_does_not_add_latency(research_env, monkeypatch):
    first = replace(tool_turn(read_call("one", 2)),
                    rate_remaining_tokens=100000, rate_reset_seconds=5)
    install_turns(monkeypatch, research_env, first, final_turn())
    monkeypatch.setattr(research_module, "sleep", lambda _: pytest.fail("Espera desnecessária"))
    result = ask(research_env)
    assert result["status"] == "answered"
    assert result["usage"]["rate_wait_ms"] == 0


@pytest.mark.parametrize("reset_seconds", [None, 5, 3600])
def test_request_above_total_rate_limit_stops_before_wait_or_new_reservation(
    research_env, monkeypatch, reset_seconds,
):
    env = research_env
    first = replace(tool_turn(read_call("one", 2)), rate_remaining_tokens=1,
                    rate_reset_seconds=reset_seconds, rate_limit_tokens=5000)
    captured = install_turns(monkeypatch, env, first)
    # Mesmo a síntese antecipada não cabe no teto informado.
    estimates = iter([4000, 6000, 5500])
    monkeypatch.setattr(research_module, "_estimated_request_tokens", lambda *args: next(estimates))
    monkeypatch.setattr(research_module, "sleep", lambda _: pytest.fail("Reset não resolve pedido grande"))
    result = ask(env)
    assert result["status"] == "research_limit"
    assert "estimativa local" in result["answer"]
    assert "limite total" in result["answer"]
    assert [source["pdf_page"] for source in result["sources"]] == [1, 2]
    assert len(captured) == len(call_rows(env)) == 1
    assert result["usage"]["rate_wait_ms"] == 0


def test_request_equal_to_total_rate_limit_can_proceed(research_env, monkeypatch):
    first = replace(tool_turn(read_call("one", 2)), rate_remaining_tokens=5000,
                    rate_reset_seconds=5, rate_limit_tokens=5000)
    install_turns(monkeypatch, research_env, first, final_turn())
    monkeypatch.setattr(research_module, "_estimated_request_tokens", lambda *args: 5000)
    monkeypatch.setattr(research_module, "sleep", lambda _: pytest.fail("Cota suficiente"))
    assert ask(research_env)["status"] == "answered"


def test_oversized_tool_history_switches_to_early_synthesis_without_losing_sources(research_env, monkeypatch):
    env = research_env
    first = replace(tool_turn(read_call("one", 2)),
                    rate_remaining_tokens=600, rate_reset_seconds=5, rate_limit_tokens=600)
    captured = install_turns(monkeypatch, env, first, final_turn("Fulgor dura uma cena [2]."))
    monkeypatch.setattr(research_module, "_estimated_request_tokens",
                        lambda messages, tools, output: 900 if tools and len(messages) > 2 else 400)
    monkeypatch.setattr(research_module, "sleep", lambda _: pytest.fail("Síntese cabe na cota atual"))
    result = ask(env)
    assert result["status"] == "answered"
    assert len(captured) == 2
    assert captured[-1]["tools"] is None
    assert [item["citation_id"] for item in result["sources"]] == [1, 2]
    assert env.texts[0] in captured[-1]["messages"][1]["content"]
    assert env.texts[1] in captured[-1]["messages"][1]["content"]
    assert any("antecipadamente" in warning for warning in result["warnings"])


def test_long_rate_reset_stops_without_spending_another_call(research_env, monkeypatch):
    first = replace(tool_turn(read_call("one", 2)),
                    rate_remaining_tokens=0, rate_reset_seconds=3600)
    install_turns(monkeypatch, research_env, first)
    monkeypatch.setattr(research_module, "sleep", lambda _: pytest.fail("Espera excessiva"))
    result = ask(research_env)
    assert result["status"] == "research_limit"
    assert len(result["sources"]) == 2
    assert len(call_rows(research_env)) == 1


def test_rate_wait_cannot_spend_research_deadline(research_env, monkeypatch):
    first = replace(tool_turn(read_call("one", 2)),
                    rate_remaining_tokens=0, rate_reset_seconds=50)
    install_turns(monkeypatch, research_env, first)
    monkeypatch.setattr(answer_module, "perf_counter", lambda: 0.0)
    monkeypatch.setattr(research_module, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(research_module, "sleep", lambda _: pytest.fail("Prazo insuficiente"))
    result = ask(research_env)
    assert result["status"] == "research_limit"
    assert len(call_rows(research_env)) == 1


def test_available_evidence_can_answer_without_forced_catalog_or_tool(research_env, monkeypatch):
    env = research_env
    with env.conn:
        env.conn.execute("UPDATE pages SET section='Lista de efeitos' WHERE pdf_page=2")
    captured = install_turns(monkeypatch, env, final_turn("O escudo protege uma pessoa [1]."))
    result = answer_module.answer_question(
        env.conn, "Compare efeitos", env.settings,
    )
    assert result["status"] == "answered"
    assert result["research"]["tool_calls"] == 0
    assert [item["pdf_page"] for item in result["sources"]] == [1]
    assert env.texts[0] in captured[0]["messages"][1]["content"]
    assert env.texts[1] not in captured[0]["messages"][1]["content"]
    assert captured[0]["tool_choice"] == "auto"
    assert result["research"]["model_calls"] == 1


def test_final_call_can_use_shorter_timeout_after_rate_wait(research_env, monkeypatch):
    env = research_env
    clock = [0.0]
    seen_timeouts = []
    monkeypatch.setattr(answer_module, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(research_module, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(research_module, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def generate(settings, messages, **kwargs):
        seen_timeouts.append(settings.timeout_seconds)
        if len(seen_timeouts) == 1:
            clock[0] = 70.0
            return replace(tool_turn(read_call("one", 2)),
                           rate_remaining_tokens=0, rate_reset_seconds=40)
        return final_turn()

    monkeypatch.setattr(research_module, "generate_turn", generate)
    result = ask(env, timeout_seconds=30)
    assert result["status"] == "answered"
    assert seen_timeouts == [30, 9.75]
    assert result["usage"]["rate_wait_ms"] == 40250


def test_quota_wait_reserves_time_for_final_answer_instead_of_more_tools(research_env, monkeypatch):
    env = research_env
    clock = [0.0]
    captured = []
    monkeypatch.setattr(answer_module, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(research_module, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(research_module, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def generate(settings, messages, **kwargs):
        captured.append({"messages": deepcopy(messages), **kwargs})
        if len(captured) == 1:
            clock[0] = 52.0
            return replace(tool_turn(read_call("read", 2)),
                           rate_remaining_tokens=0, rate_reset_seconds=25)
        assert kwargs["tools"] is None
        return final_turn("Fulgor ilumina a sala [2].")

    monkeypatch.setattr(research_module, "generate_turn", generate)
    result = ask(env, timeout_seconds=30, consult_max_model_calls=4)
    assert result["status"] == "answered"
    assert result["research"]["model_calls"] == 2
    assert result["research"]["tool_calls"] == 1
    assert env.texts[0] in captured[-1]["messages"][1]["content"]
    assert env.texts[1] in captured[-1]["messages"][1]["content"]
    assert any("reservar tempo" in warning for warning in result["warnings"])
    assert result["usage"]["rate_wait_ms"] == 25250


def test_followup_context_reaches_research_and_final_synthesis(research_env, monkeypatch):
    env = research_env
    captured = install_turns(
        monkeypatch, env, tool_turn(read_call("read", 2)),
        final_turn("Fulgor dura uma cena [2]."),
    )
    result = answer_module.answer_question(
        env.conn, "E quanto tempo ele dura?",
        replace(env.settings, consult_max_model_calls=2),
        history=[{"role": "user", "content": "Fale sobre Fulgor."},
                 {"role": "assistant", "content": "É uma luz. Fonte antiga [8]."}],
    )
    assert "Fulgor" in env.retrieval_calls[0][0]
    assert result["status"] == "answered"
    for turn in captured:
        prompt = turn["messages"][1]["content"]
        assert "E quanto tempo ele dura?" in prompt
        assert "Fale sobre Fulgor" in prompt
        assert "Respostas anteriores podem conter erros" in prompt
    assert captured[-1]["tools"] is None


def test_book_replaced_during_retrieval_never_reaches_provider(research_env, monkeypatch):
    env = research_env

    def replaced(*args, **kwargs):
        with env.conn:
            env.conn.execute("UPDATE books SET sha256='another-file'")
        return deepcopy(env.initial)

    monkeypatch.setattr(answer_module, "retrieve", replaced)
    result = ask(env)
    assert result["status"] == "book_changed"
    assert result["sources"] == []
    assert result["usage"] is None
    assert call_rows(env) == []
