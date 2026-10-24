from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_path: Path
    groq_api_key: str = field(repr=False)
    groq_model: str
    max_calls_per_day: int
    max_output_tokens: int
    timeout_seconds: float
    input_usd_per_million: float | None
    output_usd_per_million: float | None
    allow_private_book_to_groq: bool
    vector_path: Path = PROJECT_ROOT / "data" / "chroma"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    max_vector_distance: float | None = None
    reasoning_effort: str = "low"
    rerank_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3-int8"
    rerank_candidates: int = 10
    retrieval_candidate_limit: int = 30
    max_chunks_per_page: int = 2
    consult_tools_enabled: bool = True
    consult_max_model_calls: int = 4
    consult_max_tool_calls: int = 4
    consult_context_chars: int = 16000
    sheet_pdf_template: Path | None = None
    sheet_max_output_tokens: int = 4096
    sheet_request_token_budget: int = 7800
    sheet_reasoning_effort: str = "low"


def _optional_float(values: dict[str, str], name: str) -> float | None:
    value = values.get(name, "").strip()
    return float(value) if value else None


def get_settings() -> Settings:
    # Releitura permite ajustar .env enquanto a interface local está aberta.
    file_values = {k: v for k, v in dotenv_values(PROJECT_ROOT / ".env").items() if v is not None}
    # Valores explícitos do projeto prevalecem sobre chaves herdadas do sistema.
    values = {**os.environ, **file_values}
    db_path = Path(values.get("RPG_DB_PATH", "data/assistant.sqlite3"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    vector_path = Path(values.get("RPG_VECTOR_PATH", "data/chroma"))
    if not vector_path.is_absolute():
        vector_path = PROJECT_ROOT / vector_path
    reasoning_effort = values.get("GROQ_REASONING_EFFORT", "low").strip().lower()
    if reasoning_effort not in {"low", "medium", "high"}:
        raise ValueError("GROQ_REASONING_EFFORT deve ser low, medium ou high.")
    sheet_reasoning = values.get("SHEET_REASONING_EFFORT", "low").strip().lower()
    if sheet_reasoning not in {"low", "medium", "high"}:
        raise ValueError("SHEET_REASONING_EFFORT deve ser low, medium ou high.")
    return Settings(
        db_path=db_path,
        groq_api_key=values.get("GROQ_API_KEY", "").strip(),
        groq_model=values.get("GROQ_MODEL", "openai/gpt-oss-120b").strip(),
        max_calls_per_day=max(0, int(values.get("MAX_CALLS_PER_DAY", "20"))),
        max_output_tokens=max(1, int(values.get("MAX_OUTPUT_TOKENS", "2048"))),
        timeout_seconds=max(1.0, float(values.get("GROQ_TIMEOUT_SECONDS", "30"))),
        input_usd_per_million=_optional_float(values, "GROQ_INPUT_USD_PER_MILLION"),
        output_usd_per_million=_optional_float(values, "GROQ_OUTPUT_USD_PER_MILLION"),
        allow_private_book_to_groq=values.get(
            "ALLOW_PRIVATE_BOOK_TO_GROQ", "false"
        ).strip().lower() in {"1", "true", "yes", "sim"},
        vector_path=vector_path,
        embedding_model=values.get("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2").strip(),
        max_vector_distance=_optional_float(values, "MAX_VECTOR_DISTANCE"),
        reasoning_effort=reasoning_effort,
        rerank_enabled=values.get("RERANK_ENABLED", "false").strip().lower() in {"1", "true", "yes", "sim"},
        rerank_model=values.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3-int8").strip(),
        rerank_candidates=max(5, min(60, int(values.get("RERANK_CANDIDATES", "10")))),
        retrieval_candidate_limit=max(5, min(100, int(values.get("RETRIEVAL_CANDIDATE_LIMIT", "30")))),
        max_chunks_per_page=max(1, min(3, int(values.get("MAX_CHUNKS_PER_PAGE", "2")))),
        consult_tools_enabled=values.get("CONSULT_TOOLS_ENABLED", "true").strip().lower()
        in {"1", "true", "yes", "sim"},
        consult_max_model_calls=max(1, min(4, int(values.get("CONSULT_MAX_MODEL_CALLS", "4")))),
        consult_max_tool_calls=max(1, min(6, int(values.get("CONSULT_MAX_TOOL_CALLS", "4")))),
        consult_context_chars=max(4000, min(24000, int(values.get("CONSULT_CONTEXT_CHARS", "16000")))),
        sheet_pdf_template=(Path(values["SHEET_PDF_TEMPLATE"]).expanduser()
                            if values.get("SHEET_PDF_TEMPLATE", "").strip() else None),
        sheet_max_output_tokens=max(512, min(8192, int(values.get("SHEET_MAX_OUTPUT_TOKENS", "4096")))),
        sheet_request_token_budget=max(2000, min(131072, int(values.get("SHEET_REQUEST_TOKEN_BUDGET", "7800")))),
        sheet_reasoning_effort=sheet_reasoning,
    )
