"""Embeddings locais e índice vetorial persistente para o livro ativo."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from functools import lru_cache
from uuid import uuid4

import chromadb
from chromadb.config import Settings as ChromaSettings
from fastembed import TextEmbedding
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from tokenizers import Tokenizer

from .config import Settings
from .search import source


class LocalEmbeddings(Embeddings):
    """Adaptador LangChain para FastEmbed; nenhum texto é enviado à API Groq."""

    def __init__(self, model: str, cache_dir: str, local_files_only: bool = False):
        self.model = TextEmbedding(model_name=model, cache_dir=cache_dir,
                                   local_files_only=local_files_only)
        # A configuração efetiva pode ser menor que model_max_length (MiniLM:
        # max_length=128 no artefato ONNX usado). Nunca alterar o tokenizer usado
        # pela inferência, pois ele pode atender outras consultas neste processo.
        tokenizer = getattr(self.model.model, "tokenizer", None)
        self._tokenizer = None
        self._token_budget = None
        if tokenizer is not None:
            truncation = tokenizer.truncation or {}
            if truncation.get("max_length"):
                self._token_budget = (
                    truncation["max_length"] - tokenizer.num_special_tokens_to_add(False)
                )
                self._tokenizer = Tokenizer.from_str(tokenizer.to_str())
                self._tokenizer.no_truncation()
                self._tokenizer.no_padding()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text))).tolist()

    def passages(self, text: str) -> list[str]:
        """Janelas que cabem no encoder, vinculadas ao mesmo chunk de origem."""
        if self._tokenizer is None or self._token_budget is None:
            raise ValueError("Não foi possível determinar o limite efetivo do encoder.")
        offsets = self._tokenizer.encode(text, add_special_tokens=False).offsets
        if len(offsets) <= self._token_budget:
            return [text]
        budget = max(1, self._token_budget)
        overlap = min(24, budget // 4)
        result = []
        start = 0
        while start < len(offsets):
            end = min(start + budget, len(offsets))
            # Preferir a última palavra inteira dentro do orçamento.
            boundary = end
            while (boundary > start and boundary < len(offsets)
                   and offsets[boundary - 1][1] < len(text)
                   and not text[offsets[boundary - 1][1]].isspace()):
                boundary -= 1
            if boundary > start:
                end = boundary
            passage = text[offsets[start][0]:offsets[end - 1][1]].strip()
            # Um token no meio da frase pode mudar ao virar início de janela.
            # Conferir a janela isolada evita truncamento por essa diferença.
            while len(self._tokenizer.encode(passage, add_special_tokens=False).ids) > budget:
                if end <= start + 1:
                    raise ValueError("Uma unidade textual excedeu o limite do encoder.")
                end -= 1
                while (end > start + 1 and offsets[end - 1][1] < len(text)
                       and not text[offsets[end - 1][1]].isspace()):
                    end -= 1
                passage = text[offsets[start][0]:offsets[end - 1][1]].strip()
            result.append(passage)
            if end == len(offsets):
                break
            next_start = max(start + 1, end - overlap)
            while (next_start < end and offsets[next_start][0] > 0
                   and not text[offsets[next_start][0] - 1].isspace()):
                next_start += 1
            start = next_start
        return result


def _collection_name(sha256: str, model: str) -> str:
    model_hash = hashlib.sha256(model.encode()).hexdigest()[:8]
    return f"book_v2_{sha256[:16]}_{model_hash}_{uuid4().hex[:12]}"


@lru_cache(maxsize=2)
def _embeddings(model: str, cache_dir: str, local_files_only: bool = False) -> LocalEmbeddings:
    return LocalEmbeddings(model, cache_dir, local_files_only=local_files_only)


def _client(settings: Settings) -> chromadb.ClientAPI:
    settings.vector_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(settings.vector_path),
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def build_vector_index(conn: sqlite3.Connection, settings: Settings, batch_size: int = 32) -> dict:
    if batch_size < 1:
        raise ValueError("batch_size deve ser positivo.")
    book = conn.execute("SELECT id, title, sha256, imported_at FROM books LIMIT 1").fetchone()
    if book is None:
        raise ValueError("Importe um PDF antes de criar o índice vetorial.")
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    if not count:
        raise ValueError("O livro não contém trechos de texto para indexar.")
    embeddings = _embeddings(settings.embedding_model, str(settings.vector_path / "model-cache"))
    client = _client(settings)
    name = _collection_name(book["sha256"], settings.embedding_model)
    old_index = conn.execute("SELECT collection FROM vector_indices WHERE book_id = ?",
                             (book["id"],)).fetchone()
    # Construir em coleção nova: a pesquisa continua usando o índice anterior
    # até a confirmação do novo registro no SQLite.
    store = Chroma(
        client=client, collection_name=name, embedding_function=embeddings,
        collection_metadata={"hnsw:space": "cosine"},
    )
    chunks_query = """SELECT c.id, c.text, p.pdf_page, p.printed_page, p.chapter, p.section
                      FROM chunks c JOIN pages p ON p.id = c.page_id ORDER BY c.id"""
    rows = conn.execute(chunks_query)
    source_digest = hashlib.sha256()
    indexed = indexed_chunks = 0
    try:
        while batch := rows.fetchmany(batch_size):
            docs = []
            ids = []
            for row in batch:
                source_digest.update(json.dumps(tuple(row), ensure_ascii=False).encode("utf-8"))
                # Um chunk numérico legado não oferece contexto semântico.
                if not any(char.isalpha() for char in row["text"]):
                    continue
                passages = (embeddings.passages(row["text"]) if hasattr(embeddings, "passages")
                            else [row["text"]])
                for window, passage in enumerate(passages):
                    docs.append(Document(
                        page_content=passage,
                        metadata={"chunk_id": row["id"], "window": window,
                                  "pdf_page": row["pdf_page"],
                                  "printed_page": row["printed_page"] or "",
                                  "chapter": row["chapter"] or "", "section": row["section"] or ""},
                    ))
                    ids.append(f"{row['id']}:{window}")
                indexed_chunks += 1
            for start in range(0, len(docs), batch_size):
                store.add_documents(docs[start:start + batch_size],
                                    ids=ids[start:start + batch_size])
            indexed += len(docs)
        if not indexed:
            raise ValueError("Não há trechos textuais úteis para indexar.")
        # A importação usa o mesmo banco; verificar sob lock antes de publicar.
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT id, title, sha256, imported_at FROM books LIMIT 1"
        ).fetchone()
        if current is None or tuple(current) != tuple(book):
            raise ValueError("O livro mudou durante a indexação. Reconstrua o índice.")
        current_digest = hashlib.sha256()
        for row in conn.execute(chunks_query):
            current_digest.update(json.dumps(tuple(row), ensure_ascii=False).encode("utf-8"))
        if current_digest.digest() != source_digest.digest():
            raise ValueError("Os trechos mudaram durante a indexação. Reconstrua o índice.")
        conn.execute(
            "INSERT OR REPLACE INTO vector_indices(book_id, model, collection, chunk_count) "
            "VALUES (?, ?, ?, ?)", (book["id"], settings.embedding_model, name, indexed_chunks),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        # Apenas a coleção temporária criada nesta operação é removida.
        client.delete_collection(name)
        raise
    # Não remover imediatamente a coleção anterior: consultas já iniciadas podem
    # estar usando-a. A limpeza das coleções substituídas é uma tarefa de manutenção.
    return {"book": book["title"], "model": settings.embedding_model,
            "vectors": indexed, "chunks": indexed_chunks, "collection": name,
            "previous_collection": old_index["collection"] if old_index else None,
            "index_version": 2}


def vector_search(conn: sqlite3.Connection, question: str, settings: Settings,
                  limit: int = 10) -> list[dict]:
    index = conn.execute(
        "SELECT model, collection FROM vector_indices LIMIT 1"
    ).fetchone()
    if index is None:
        return []
    # Consultas nunca aguardam downloads/verificações remotas de pesos. A
    # preparação explícita do índice é quem pode baixar o encoder.
    embeddings = _embeddings(index["model"], str(settings.vector_path / "model-cache"), True)
    store = Chroma(client=_client(settings), collection_name=index["collection"],
                   embedding_function=embeddings, create_collection_if_not_exists=False)
    # A mesma origem pode ter várias janelas; recuperar uma margem e deduplicar.
    matches = store.similarity_search_with_score(question, k=min(limit * 3, 150))
    results = []
    seen = set()
    for doc, distance in matches:
        chunk_id = int(doc.metadata["chunk_id"])
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        row = source(conn, chunk_id)
        if row and any(char.isalpha() for char in row["text"]):
            row.pop("pdf_path")
            row["distance"] = float(distance)
            results.append(row)
            if len(results) >= limit:
                break
    return results
