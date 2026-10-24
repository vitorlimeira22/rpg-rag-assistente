"""Cross-encoder local opcional. Preparação explícita evita downloads no clique."""

from __future__ import annotations

from functools import lru_cache
import json
import math
from threading import Lock

from fastembed.rerank.cross_encoder import TextCrossEncoder

from .config import Settings


DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3-int8"
_load_lock = Lock()


class RerankUnavailable(RuntimeError):
    """O ranking híbrido continua utilizável; a falha deve aparecer no diagnóstico."""


@lru_cache(maxsize=1)
def _encoder(model_name: str, cache_dir: str) -> TextCrossEncoder:
    return TextCrossEncoder(
        model_name=model_name, cache_dir=cache_dir, threads=4,
        providers=["CPUExecutionProvider"], local_files_only=True,
    )


def rerank_candidates(question: str, candidates: list[dict], settings: Settings) -> list[dict]:
    """Pontua pergunta + trecho, sem API externa nem interpretação como instruções."""
    if not candidates:
        return []
    try:
        with _load_lock:
            encoder = _encoder(
                getattr(settings, "rerank_model", DEFAULT_MODEL),
                str(settings.vector_path / "reranker-cache"),
            )
        passages = [
            f"{row.get('chapter') or ''} / {row.get('section') or ''}\n{row['text']}"
            for row in candidates
        ]
        scores = list(encoder.rerank(question, passages, batch_size=4))
        if len(scores) != len(candidates) or not all(math.isfinite(float(s)) for s in scores):
            raise ValueError("Pontuações incompletas ou não finitas.")
    except Exception as exc:
        # Não expor caminhos locais, conteúdo do material ou detalhes internos.
        raise RerankUnavailable("Cross-encoder local indisponível.") from exc
    scored = [
        {**row, "rerank_score": float(score), "methods": [*row["methods"], "reranking"]}
        for row, score in zip(candidates, scores)
    ]
    return sorted(scored, key=lambda row: row["rerank_score"], reverse=True)


def prepare_reranker(settings: Settings) -> dict:
    """Baixa arquivos públicos do modelo e executa uma inferência sintética."""
    model_name = getattr(settings, "rerank_model", DEFAULT_MODEL)
    cache_dir = str(settings.vector_path / "reranker-cache")
    # lazy_load baixa os arquivos; a sessão de inferência será a instância cacheada.
    TextCrossEncoder(model_name=model_name, cache_dir=cache_dir, lazy_load=True,
                     providers=["CPUExecutionProvider"])
    with _load_lock:
        encoder = _encoder(model_name, cache_dir)
    scores = list(encoder.rerank(
        "Onde guardar um livro?",
        ["Um livro pode ficar em uma estante.", "A chuva molhou a rua."],
        batch_size=2,
    ))
    if len(scores) != 2 or not all(math.isfinite(float(score)) for score in scores):
        raise RerankUnavailable("O teste de preparação não retornou pontuações válidas.")
    return {"model": model_name, "ready": True, "device": "cpu"}


if __name__ == "__main__":
    from .config import get_settings

    print(json.dumps(prepare_reranker(get_settings()), ensure_ascii=False))
