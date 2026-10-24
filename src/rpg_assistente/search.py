from __future__ import annotations

import re
import sqlite3
from collections import Counter
from time import perf_counter
import unicodedata

from .config import Settings


STOPWORDS = {
    "a", "as", "o", "os", "de", "da", "das", "do", "dos", "e", "em", "um",
    "uma", "para", "por", "que", "qual", "quais", "como", "com", "no", "na",
    "nos", "nas", "se", "ser", "sao", "é", "eu", "me", "minha", "meu",
    "funciona", "funcionam", "pode", "posso", "sobre", "regra", "regras",
    "quanto", "custa", "custo", "existe", "usar", "fazer",
    "ao", "aos", "ou", "ja", "ha", "foi", "um", "uma",
}


def _terms(question: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", question.lower())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    terms = re.findall(r"[\w]+", normalized, flags=re.UNICODE)
    # Siglas curtas (PE, PV, DT...) fazem parte do vocabulário de regras.
    return list(dict.fromkeys(t for t in terms
                              if len(t) >= 2 and t not in STOPWORDS
                              and any(char.isalpha() for char in t)))[:16]


def search(conn: sqlite3.Connection, question: str, limit: int = 5,
           partial: bool = False, diversify: bool = True) -> list[dict]:
    if limit < 1:
        return []
    terms = _terms(question)
    if not terms:
        return []
    # Exigir todos os termos reduz falsos positivos em dúvidas sobre regras inexistentes.
    if partial:
        expression = " OR ".join(f'"{term}"' for term in terms)
    else:
        expression = " AND ".join(f'"{term}"' for term in terms)
    rows = conn.execute(
        """SELECT c.id, c.text, p.pdf_page, p.printed_page, p.chapter, p.section,
                  b.title, b.edition, bm25(chunks_fts) AS score
           FROM chunks_fts
           JOIN chunks c ON c.id = chunks_fts.rowid
           JOIN pages p ON p.id = c.page_id
           JOIN books b ON b.id = p.book_id
           WHERE chunks_fts MATCH ?
           ORDER BY score LIMIT ?""",
        (expression, max(1, min(limit * 5, 150))),
    ).fetchall()
    results = []
    seen_pages = set()
    for row in rows:
        page_key = (row["title"], row["edition"], row["pdf_page"])
        if not any(char.isalpha() for char in row["text"]):
            continue
        if diversify and page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        results.append(dict(row))
        if len(results) >= limit:
            break
    return results


def hybrid_search(conn: sqlite3.Connection, question: str, settings: Settings,
                  limit: int = 5) -> list[dict]:
    """Interface compatível para CLI e avaliação; retrieve também traz diagnósticos."""
    return retrieve(conn, question, settings, limit)["sources"]


def _redundant(row: dict, accepted: list[dict]) -> bool:
    """Evita gastar contexto em duas janelas quase iguais da mesma página."""
    words = set(re.findall(r"\w+", row["text"].lower()))
    for other in accepted:
        if (row["title"], row["edition"], row["pdf_page"]) != (
            other["title"], other["edition"], other["pdf_page"]
        ):
            continue
        other_words = set(re.findall(r"\w+", other["text"].lower()))
        union = words | other_words
        if union and len(words & other_words) / len(union) > 0.85:
            return True
    return False


def retrieve(conn: sqlite3.Connection, question: str, settings: Settings,
             limit: int = 5) -> dict:
    """Busca híbrida, reranking opcional e diagnóstico de degradação explícito."""
    from .vector import vector_search
    from .rerank import RerankUnavailable, rerank_candidates

    started = perf_counter()
    warnings = []
    candidate_limit = min(100, max(5, getattr(settings, "retrieval_candidate_limit", 30)))
    strict = search(conn, question, limit=candidate_limit, diversify=False)
    broad = search(conn, question, limit=candidate_limit, partial=True, diversify=False)
    # Resultados exatos têm prioridade; OR complementa mesmo quando AND encontra
    # poucos trechos. Cada chunk participa só uma vez do ranking lexical.
    lexical = list({row["id"]: row for row in [*strict, *broad]}.values())[:candidate_limit]
    lexical_ms = round((perf_counter() - started) * 1000)
    semantic_started = perf_counter()
    try:
        semantic = vector_search(conn, question, settings, limit=candidate_limit)
    except Exception:
        semantic = []
        warnings.append("Busca vetorial indisponível nesta consulta; foram usadas apenas fontes textuais.")
    semantic_ms = round((perf_counter() - semantic_started) * 1000)
    if conn.execute("SELECT 1 FROM vector_indices LIMIT 1").fetchone() is None:
        warnings.append("Índice vetorial ainda não preparado; a busca está usando correspondência textual.")
    candidates: dict[int, dict] = {}
    for method, rows in (("textual", lexical), ("semântica", semantic)):
        for rank, row in enumerate(rows, 1):
            # Recuperação seleciona candidatos; suficiência é avaliada na resposta.
            # Distância não é probabilidade de o trecho responder à pergunta.
            if (method == "semântica" and settings.max_vector_distance is not None
                    and row["distance"] > settings.max_vector_distance):
                continue
            if not any(char.isalpha() for char in row["text"]):
                continue
            entry = candidates.setdefault(row["id"], {**row, "rank_score": 0.0, "methods": []})
            entry["rank_score"] += 1 / (60 + rank)
            entry["methods"].append(method)
            if "distance" in row:
                entry["distance"] = row["distance"]
    ranked = sorted(candidates.values(), key=lambda row: row["rank_score"], reverse=True)
    rerank_status = "disabled"
    rerank_ms = 0
    if getattr(settings, "rerank_enabled", False) and ranked:
        rerank_started = perf_counter()
        cutoff = min(100, max(limit, getattr(settings, "rerank_candidates", 10)))
        try:
            # Diversificação acontece abaixo. Se vários candidatos reordenados
            # pertencem à mesma página, os demais ainda podem completar o limite
            # solicitado; não descartá-los antes dessa seleção.
            ranked = [*rerank_candidates(question, ranked[:cutoff], settings),
                      *ranked[cutoff:]]
            rerank_status = "applied"
        except RerankUnavailable:
            rerank_status = "unavailable"
            warnings.append(
                "Reranking indisponível; mantida a ordem da busca híbrida. "
                "Prepare o modelo local conforme o README."
            )
        rerank_ms = round((perf_counter() - rerank_started) * 1000)
    result = []
    page_counts = Counter()
    max_per_page = max(1, getattr(settings, "max_chunks_per_page", 2))
    for row in ranked:
        page = (row["title"], row["edition"], row["pdf_page"])
        if page_counts[page] >= max_per_page or _redundant(row, result):
            continue
        page_counts[page] += 1
        row.pop("rank_score")
        row.pop("score", None)
        result.append(row)
        if len(result) >= limit:
            break
    if limit < 1:
        result = []
    return {
        "sources": result, "warnings": warnings,
        "retrieval": {
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "lexical_ms": lexical_ms, "semantic_ms": semantic_ms, "rerank_ms": rerank_ms,
            "lexical_candidates": len(lexical), "semantic_candidates": len(semantic),
            "rerank_status": rerank_status,
            "rerank_model": getattr(settings, "rerank_model", None) if rerank_status != "disabled" else None,
        },
    }


def source(conn: sqlite3.Connection, chunk_id: int) -> dict | None:
    row = conn.execute(
        """SELECT c.id, c.text, p.pdf_page, p.printed_page, p.chapter, p.section,
                  b.title, b.edition, b.pdf_path
           FROM chunks c JOIN pages p ON p.id = c.page_id
           JOIN books b ON b.id = p.book_id WHERE c.id = ?""",
        (chunk_id,),
    ).fetchone()
    return dict(row) if row else None
