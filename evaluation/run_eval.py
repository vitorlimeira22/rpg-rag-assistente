"""Avalia a recuperação de fontes sem usar nem cobrar a API de IA."""

import json
import sys
from pathlib import Path

from rpg_assistente.config import get_settings
from rpg_assistente.db import connect
from rpg_assistente.search import hybrid_search


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cases = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
    settings = get_settings()
    conn = connect(settings.db_path)
    passed = 0
    evaluated = 0
    try:
        for case in cases:
            found = hybrid_search(conn, case["question"], settings)
            pages = [item["pdf_page"] for item in found]
            expected = set(case["expected_pdf_pages"])
            if case.get("requires_abstention"):
                print(f"REVISAR RESPOSTA | {case['question']} | Candidatos: {pages}. Não inventar resposta.")
                continue
            evaluated += 1
            ok = bool(expected.intersection(pages))
            passed += ok
            print(f"{'OK' if ok else 'FALHOU'} | {case['question']} | PDF páginas: {pages}")
    finally:
        conn.close()
    print(f"Recuperação: {passed}/{evaluated} casos com fonte esperada; abstenção exige avaliação da resposta.")
    if passed != evaluated:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
