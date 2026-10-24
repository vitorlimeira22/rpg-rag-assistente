"""Smoke test público de ingestão e recuperação, isolado dos dados pessoais.

Não lê .env, não baixa modelos e não chama IA. Não mede qualidade da geração.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from rpg_assistente.db import connect
from rpg_assistente.config import Settings
from rpg_assistente.ingest import import_pdf
from rpg_assistente.search import retrieve


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cases = json.loads((ROOT / "evaluation/demo-questions.json").read_text(encoding="utf-8"))
    passed = 0
    with TemporaryDirectory(prefix="rpg-public-demo-") as directory:
        db = Path(directory) / "demo.sqlite3"
        settings = Settings(
            db_path=db, vector_path=Path(directory) / "chroma", groq_api_key="", groq_model="",
            max_calls_per_day=0, max_output_tokens=1, timeout_seconds=1,
            input_usd_per_million=None, output_usd_per_million=None, allow_private_book_to_groq=False,
        )
        import_pdf(ROOT / "examples/demo_rules.pdf", db, "Crônicas do Farol", "0.1")
        conn = connect(db)
        try:
            for case in cases:
                rows = retrieve(conn, case["question"], settings)["sources"]
                pages = {row["pdf_page"] for row in rows}
                ok = bool(pages.intersection(case["expected_pdf_pages"]))
                passed += ok
                print(f"{'OK' if ok else 'FALHOU'}: {case['question']} | páginas {sorted(pages)}")
        finally:
            conn.close()
    print(f"Recuperação textual pública: {passed}/{len(cases)}. Geração, abstenção e busca vetorial não avaliadas aqui.")
    if passed != len(cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
