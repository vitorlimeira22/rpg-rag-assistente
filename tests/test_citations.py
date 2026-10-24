"""Citações são saída não confiável, inclusive em respostas já concluídas."""

import pytest

import rpg_assistente.answer as answer_module
import rpg_assistente.research as research_module
from rpg_assistente.citations import citation_status, normalize_citations
from rpg_assistente.config import Settings
from rpg_assistente.db import connect
from rpg_assistente.llm import GenerationTurn


@pytest.mark.parametrize("text,count,expected", [
    ("Texto sem referência.", 3, "absent"),
    ("Regra [1], condição [3] e novamente [1].", 3, "present"),
    ("Referência [4].", 3, "invalid"),
    ("Válida [1], inválida [0].", 3, "invalid"),
    ("Não existem fontes [1].", 0, "invalid"),
    ("Zeros à esquerda [0002].", 3, "present"),
    ("Dígitos decimais Unicode [٢] e [３].", 3, "present"),
    ("[" + "9" * 5000 + "]", 3, "invalid"),
    ("[" + "٩" * 5000 + "]", 3, "invalid"),
    ("[" + "0" * 5000 + "1]", 3, "present"),
])
def test_citation_ids_remain_bounded_without_large_integer_conversion(text, count, expected):
    assert citation_status(text, count) == expected


@pytest.mark.parametrize("text,expected", [
    ("Regra 【7】 e condição 【8†】.", "Regra [7] e condição [8]."),
    ("Unicode 【٧†】 e 【０８】.", "Unicode [7] e [8]."),
    ("Zeros 【0007】 e 【0†】.", "Zeros [7] e [0]."),
    ("Texto comum, [7], [٧] e dados 42.", "Texto comum, [7], [٧] e dados 42."),
    ("【https://example.com/7】 e 【7†https://example.com】.",
     "【https://example.com/7】 e 【7†https://example.com】."),
    ("【Livro】 【7†Livro】 【7,8】 【7 †】 【 7】 【7††】",
     "【Livro】 【7†Livro】 【7,8】 【7 †】 【 7】 【7††】"),
])
def test_only_equivalent_numeric_citation_formats_are_normalized(text, expected):
    assert normalize_citations(text) == expected


@pytest.mark.parametrize("text", ["【8】", "【٨†】", "【0】", "【" + "9" * 5000 + "†】"])
def test_normalization_does_not_approve_nonexistent_source_ids(text):
    normalized = normalize_citations(text)
    assert normalized.startswith("[") and normalized.endswith("]")
    assert citation_status(normalized, 7) == "invalid"


@pytest.mark.parametrize("tools_enabled", [False, True])
def test_long_invalid_citation_preserves_sources_and_audit_in_both_paths(
    tmp_path, monkeypatch, tools_enabled,
):
    settings = Settings(
        db_path=tmp_path / "citations.sqlite3", groq_api_key="test-key",
        groq_model="test-model", max_calls_per_day=2, max_output_tokens=2048,
        timeout_seconds=10, input_usd_per_million=None, output_usd_per_million=None,
        allow_private_book_to_groq=True, consult_tools_enabled=tools_enabled,
    )
    initial = {
        "sources": [{
            "id": 1, "title": "Livro fictício", "edition": "1",
            "text": "Regra fictícia usada somente pelo teste.",
            "pdf_page": 1, "printed_page": "1", "chapter": "Regras",
            "section": None, "methods": ["textual"],
        }],
        "warnings": [], "retrieval": {"elapsed_ms": 0},
    }
    response = GenerationTurn("Referência inválida [" + "9" * 5000 + "].", 10, 20, "stop")
    monkeypatch.setattr(answer_module, "retrieve", lambda *args, **kwargs: initial)
    monkeypatch.setattr(answer_module, "generate_reply", lambda *args, **kwargs: response)
    monkeypatch.setattr(research_module, "generate_turn", lambda *args, **kwargs: response)
    conn = connect(settings.db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO books(title,edition,pdf_path,sha256,page_count) VALUES (?,?,?,?,?)",
                ("Livro fictício", "1", str(tmp_path / "fiction.pdf"), "synthetic-hash", 1),
            )
        result = answer_module.answer_question(conn, "Consulte a regra.", settings)
        assert result["status"] == "needs_review"
        assert result["citation_check"] == "invalid"
        assert result["sources"][0]["text"] == initial["sources"][0]["text"]
        assert result["usage"]["input_tokens"] == 10
        assert result["warnings"]
        assert conn.execute("SELECT citation_status FROM llm_call_context").fetchone()[0] == "invalid"
        assert conn.execute("SELECT status FROM llm_calls").fetchone()[0] == "ok"
    finally:
        conn.close()
