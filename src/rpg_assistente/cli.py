from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .answer import answer_question
from .config import get_settings
from .db import connect
from .ingest import import_pdf
from .search import hybrid_search
from .vector import build_vector_index


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Assistente local de RPG")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Extrair e indexar um PDF com texto selecionável")
    importer.add_argument("--pdf", type=Path, required=True)
    importer.add_argument("--title", required=True)
    importer.add_argument("--edition", required=True)
    commands.add_parser("index-vectors", help="Criar embeddings locais e índice Chroma")
    commands.add_parser("refresh-text", help="Reextrair o mesmo PDF, preservando fichas e conversas; requer novo índice vetorial")
    commands.add_parser("prepare-reranker", help="Baixar e conferir o cross-encoder local")
    finder = commands.add_parser("search", help="Buscar trechos com fontes")
    finder.add_argument("question")
    asker = commands.add_parser("ask", help="Responder usando os trechos recuperados")
    asker.add_argument("question")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "import":
        result = import_pdf(args.pdf, settings.db_path, args.title, args.edition,
                            settings.vector_path)
    elif args.command == "prepare-reranker":
        from .rerank import prepare_reranker
        result = prepare_reranker(settings)
    else:
        conn = connect(settings.db_path)
        try:
            if args.command == "refresh-text":
                from .maintenance import refresh_book_text
                result = refresh_book_text(conn)
            elif args.command == "index-vectors":
                result = build_vector_index(conn, settings)
            elif args.command == "search":
                result = hybrid_search(conn, args.question, settings)
            else:
                result = answer_question(conn, args.question, settings)
        finally:
            conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
