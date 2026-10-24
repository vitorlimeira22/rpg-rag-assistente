"""Avaliação real por HTTP; o gabarito nunca é enviado ao assistente.

Execute com o servidor local ativo. Sem --live, apenas lista os casos.
Respostas e fontes privadas ficam em data/evaluations, fora do Git.
A revisão factual é manual, comparando cada resposta ao gabarito e ao PDF.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Chama a aplicação e consome cota Groq")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cases", help="IDs separados por vírgula; padrão: todos")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "evaluation/build-mechanics-ordem-1.1.json").read_text("utf-8"))
    if args.cases:
        selected = set(args.cases.split(","))
        if not selected <= {c["id"] for c in cases}:
            parser.error("ID de caso desconhecido")
        cases = [c for c in cases if c["id"] in selected]
    if not args.live:
        print(json.dumps(cases, ensure_ascii=False, indent=2))
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "data/evaluations" / f"{stamp}-build-mechanics"
    output.mkdir(parents=True, exist_ok=False)
    print(f"Resultados privados: {output}", flush=True)
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=180, trust_env=False) as client:
        status = client.get("/api/status")
        status.raise_for_status()
        state = status.json()
        book = state.get("book") or {}
        if "Ordem Paranormal" not in book.get("title", "") or book.get("edition") != "1.1":
            raise SystemExit("A avaliação requer Ordem Paranormal 1.1 como livro ativo.")
        (output / "status.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        for case in cases:
            print(f"Iniciando {case['id']}", flush=True)
            started = time.perf_counter()
            response = client.post("/api/ask", json={"question": case["question"]})
            response.raise_for_status()
            result = response.json()
            record = {"case": case, "elapsed_seconds": round(time.perf_counter() - started, 2),
                      "result": result}
            (output / f"{case['id']}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), "utf-8",
            )
            print(json.dumps({"case": case["id"], "status": result.get("status"),
                              "seconds": record["elapsed_seconds"], "usage": result.get("usage"),
                              "pages": [s["pdf_page"] for s in result.get("sources", [])]},
                             ensure_ascii=False), flush=True)
            if result.get("status") in {"api_error", "daily_limit", "sources_only", "private_book_local_only"}:
                print("Avaliação interrompida por erro ou indisponibilidade da API; não repetiu chamadas.", flush=True)
                break


if __name__ == "__main__":
    main()
