"""Experimento local: separar recuperação, geração e pesquisa por ferramentas.

Sem --live, prepara e confere os contextos sem chamar o Groq.
Saídas com texto privado ficam somente em data/evaluations/ (ignorado pelo Git).
Não atribui nota factual automaticamente: use a rubrica e confira as fontes.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from unittest.mock import patch

import groq
import httpx

from rpg_assistente import llm
from rpg_assistente.answer import INSTRUCTIONS, PROMPT_VERSION, answer_question
from rpg_assistente.config import PROJECT_ROOT, get_settings
from rpg_assistente.db import connect
from rpg_assistente.search import retrieve


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def reference_sources(conn, case_id: str, manifest: dict) -> list[dict]:
    """Falha se um delimitador não for único: nunca usa uma fatia presumida."""
    sources = []
    for spec in manifest["cases"][case_id]:
        row = conn.execute(
            """SELECT (SELECT id FROM chunks WHERE page_id=p.id
                       ORDER BY position LIMIT 1) AS id,
                      b.title,b.edition,p.pdf_page,p.printed_page,p.chapter,p.section,p.text
               FROM pages p JOIN books b ON b.id=p.book_id
               WHERE p.pdf_page=? AND b.id=(SELECT id FROM books ORDER BY id LIMIT 1)""",
            (spec["pdf_page"],),
        ).fetchone()
        if row is None:
            raise ValueError(f"Página ausente: {spec['pdf_page']}")
        source = dict(row)
        content = source["text"]
        for delimiter, keep_after in ((spec.get("start"), True), (spec.get("end"), False)):
            if delimiter is None:
                continue
            if content.count(delimiter) != 1:
                raise ValueError(f"Delimitador ausente/ambíguo: {case_id}, página {spec['pdf_page']}")
            offset = content.index(delimiter)
            content = content[offset:] if keep_after else content[:offset]
        if not content.strip():
            raise ValueError("Fonte vazia")
        content = content.strip()
        if (len(content) != spec["expected_chars"] or
                hashlib.sha256(content.encode("utf-8")).hexdigest() != spec["sha256"]):
            raise ValueError(f"Texto de referência mudou: {case_id}, página {spec['pdf_page']}")
        source.update(text=content.strip(), methods=["referência manual de avaliação"])
        sources.append(source)
    return sources


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", choices=["retrieved", "reference", "agent"], required=True)
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--cases", help="IDs separados por vírgula; padrão: todos")
    parser.add_argument("--live", action="store_true", help="Envia contexto ao Groq e consome cota")
    parser.add_argument("--snapshot", type=Path, help="Reutiliza contextos já congelados")
    parser.add_argument("--label", default="comparison")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("comparisons-ordem-paranormal-1.1.json"))
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("reference-contexts.json"))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", args.label):
        parser.error("--label deve conter só letras, números, hífen ou sublinhado")
    if args.snapshot and args.context == "agent":
        parser.error("O agente decide suas próprias fontes; não admite snapshot fixo")

    settings = get_settings()
    settings = replace(settings, reasoning_effort=args.effort,
                       consult_tools_enabled=args.context == "agent")
    if args.model:
        settings = replace(settings, groq_model=args.model,
                           input_usd_per_million=None, output_usd_per_million=None)
    dataset = json.loads(args.dataset.read_text("utf-8"))
    cases = dataset if isinstance(dataset, list) else dataset["cases"]
    if args.cases:
        requested = set(args.cases.split(","))
        cases = [case for case in cases if case["id"] in requested]
        if {case["id"] for case in cases} != requested:
            parser.error("ID de caso desconhecido")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = PROJECT_ROOT / "data" / "evaluations" / f"{timestamp}-{args.label}"
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = (json.loads(args.manifest.read_text("utf-8"))
                if args.context == "reference" and not args.snapshot else None)
    frozen = json.loads(args.snapshot.read_text("utf-8")) if args.snapshot else None
    with connect(settings.db_path) as conn:
        book = dict(conn.execute("SELECT title,edition,sha256 FROM books LIMIT 1").fetchone())
        if frozen and frozen["book"] != book:
            raise ValueError("Snapshot pertence a outro livro/edição/arquivo")
        if manifest and manifest["book"] != book:
            raise ValueError("Manifesto pertence a outro livro/edição/arquivo")
        contexts = {}
        for case in cases:
            if args.context == "agent":
                continue
            if frozen:
                item = frozen["cases"][case["id"]]
                if item["question"] != case["question"]:
                    raise ValueError("Pergunta mudou desde o snapshot")
                retrieval = item["retrieval"]
            elif args.context == "reference":
                retrieval = {"sources": reference_sources(conn, case["id"], manifest),
                             "warnings": [], "retrieval": {"origin": "manual_reference"}}
            else:
                retrieval = retrieve(conn, case["question"], settings, limit=5)
            contexts[case["id"]] = {"question": case["question"], "retrieval": retrieval}
        snapshot = {"book": book, "cases": contexts}
        (output_dir / "contexts.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), "utf-8")
        metadata = {
            "created_at": timestamp, "book": book, "context": args.context,
            "model": settings.groq_model, "reasoning_effort": settings.reasoning_effort,
            "max_output_tokens": settings.max_output_tokens, "temperature": "provider_default",
            "prompt_version": PROMPT_VERSION, "prompt_sha256": digest(INSTRUCTIONS),
            "dataset_sha256": digest(dataset), "contexts_sha256": digest(snapshot),
            "live": args.live, "quality": "requires_manual_review",
            "vector_index": [dict(row) for row in conn.execute("SELECT * FROM vector_indices")],
        }
        (output_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps({"output_dir": str(output_dir), "contexts": {
            key: {"pages": [s["pdf_page"] for s in item["retrieval"]["sources"]],
                  "characters": sum(len(s["text"]) for s in item["retrieval"]["sources"])}
            for key, item in contexts.items()}}, ensure_ascii=False), flush=True)
        if not args.live:
            return
        original_turn = llm.generate_turn
        reset_at = 0.0
        for case in cases:
            # Entre casos, deixa o bucket informado pelo provedor se recompor.
            # Não repete falhas nem oculta tentativas extras do contador da aplicação.
            remaining_wait = reset_at - time.monotonic()
            if remaining_wait > 0:
                if remaining_wait > 60:
                    print("Pausa necessária maior que 60 s; execute os casos restantes depois.", flush=True)
                    break
                print(json.dumps({"wait_seconds": round(remaining_wait, 2)}), flush=True)
                time.sleep(remaining_wait)
            trace = []
            provider_limits = []

            def capture_limits(response):
                if response.status_code != 429:
                    return
                # Inspeciona a causa para distinguir TPM de TPD. Nunca grava
                # corpo livre, credenciais, prompts ou cabeçalhos arbitrários.
                response.read()
                body = response.text.lower()
                scope = next((name for marker, name in (
                    ("tokens per day", "tokens_per_day"),
                    ("tokens per minute", "tokens_per_minute"),
                    ("requests per day", "requests_per_day"),
                    ("requests per minute", "requests_per_minute"),
                ) if marker in body), "unknown")
                retry = response.headers.get("retry-after", "")
                provider_limits.append({
                    "status": 429, "scope": scope,
                    "retry_after_seconds": float(retry)
                    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", retry) else None,
                })

            def traced(*turn_args, **turn_kwargs):
                nonlocal reset_at
                response = original_turn(*turn_args, **turn_kwargs)
                trace.append({
                    "messages_sha256": digest(turn_args[1] if len(turn_args) > 1 else turn_kwargs["messages"]),
                    "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                    "finish_reason": response.finish_reason,
                    "tools": [{"name": call.name, "arguments": call.arguments}
                              for call in response.tool_calls],
                    "remaining_tokens": response.rate_remaining_tokens,
                    "limit_tokens": response.rate_limit_tokens,
                    "reset_seconds": response.rate_reset_seconds,
                })
                reset_at = time.monotonic() + (response.rate_reset_seconds or 0) + 0.5
                return response

            # Usa as mesmas reservas de cota, permissões e logs da aplicação.
            with groq.Groq(api_key=settings.groq_api_key, max_retries=0,
                           timeout=settings.timeout_seconds,
                           http_client=httpx.Client(timeout=settings.timeout_seconds,
                                                    event_hooks={"response": [capture_limits]})) as client, \
                    patch("rpg_assistente.llm.generate_turn", traced), \
                    patch("rpg_assistente.research.generate_turn", traced):
                if args.context == "agent":
                    result = answer_question(conn, case["question"], settings, client=client)
                else:
                    with patch("rpg_assistente.answer.retrieve",
                               return_value=contexts[case["id"]]["retrieval"]):
                        result = answer_question(conn, case["question"], settings, client=client)
            record = {"case_id": case["id"], "question": case["question"],
                      "trace": trace, "provider_limits": provider_limits,
                      "result": result, "review": None}
            (output_dir / f"{case['id']}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
            print(json.dumps({"case_id": case["id"], "status": result["status"],
                              "answer": result["answer"], "usage": result.get("usage"),
                              "pages": [s["pdf_page"] for s in result.get("sources", [])],
                              "trace": trace, "provider_limits": provider_limits},
                             ensure_ascii=False), flush=True)
            if provider_limits or result.get("error_code") == 429 or result["status"] == "daily_limit":
                print("Limite atingido; experimento interrompido, sem novas tentativas.", flush=True)
                break


if __name__ == "__main__":
    main()
