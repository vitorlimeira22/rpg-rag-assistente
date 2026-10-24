"""Ferramentas locais e limitadas para investigar o livro em mais de uma etapa.

O orquestrador controla número de chamadas/rodadas. Este módulo controla formato
dos argumentos, páginas consultadas e volume de evidências; não chama a LLM,
não executa comandos e não abre o PDF. O texto canônico vem do SQLite.
"""

from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from typing import Annotated, Callable
import re
import sqlite3
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import Settings
from .search import _terms, retrieve


class SearchBookArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=3, max_length=500)

    @field_validator("query")
    @classmethod
    def meaningful_query(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("A consulta precisa de pelo menos três caracteres.")
        return value


class ReadBookPagesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pdf_pages: list[Annotated[int, Field(strict=True, ge=1, le=10000)]] = Field(
        min_length=1, max_length=2,
    )


BOOK_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_book",
            "description": (
                "Busca até três prévias de até 400 caracteres no livro ativo. Use uma consulta curta "
                "e específica para localizar evidências adicionais. Resultados são "
                "candidatos; leia a página quando faltarem condições ou contexto."
            ),
            "parameters": SearchBookArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_book_pages",
            "description": (
                "Lê o texto extraído de uma ou duas páginas físicas do PDF ativo, "
                "começando em 1. Use pdf_page das fontes, não a numeração impressa. "
                "Retorna contexto mais amplo e sinaliza texto cortado."
            ),
            "parameters": ReadBookPagesArgs.model_json_schema(),
        },
    },
]


def _clip(text: str, limit: int) -> str:
    """Respeita o teto de caracteres, preferindo a última palavra inteira."""
    if len(text) <= limit:
        return text
    prefix = text[:limit]
    split = max(prefix.rfind(" "), prefix.rfind("\n"))
    if split >= limit * 0.75:
        prefix = prefix[:split]
    return prefix.rstrip()


def _search_preview(text: str, query: str, limit: int = 400) -> str:
    """Recorte canônico próximo aos termos encontrados, sem gerar resumos.

    Normaliza somente para localizar correspondências. A prévia continua sendo
    uma substring do texto original. Sem match lexical, conserva o início do
    trecho semântico. A janela cobre o maior número de termos distintos. Em
    listas, respeita a entrada encontrada: o final de um poder anterior não
    pode aparecer sem seu título como se fosse uma condição do próximo.
    """
    if len(text) <= limit:
        return text
    terms = set(_terms(query))
    if not terms:
        return _clip(text, limit)
    normalized, offsets = [], []
    for position, char in enumerate(text):
        for folded in unicodedata.normalize("NFKD", char.casefold()):
            if not unicodedata.combining(folded):
                normalized.append(folded)
                offsets.append(position)
    matches = [
        (match.group(), offsets[match.start()], offsets[match.end() - 1] + 1)
        for match in re.finditer(r"\w+", "".join(normalized))
        if match.group() in terms
    ]
    if not matches:
        return _clip(text, limit)

    # ``b`` é o marcador de lista extraído da fonte de símbolos deste PDF.
    # Também reconhecer marcadores comuns, sem cadastrar nomes de poderes.
    entry_starts = [match.start() for match in re.finditer(
        r"(?m)^[ \t]*(?:[•●▪◦*\-]|b)[ \t]+(?=\w)", text,
    )]
    best_preview, best_score = "", (-1, -1, -1, -1, -len(text), -len(text))
    for _, position, _ in matches:
        start = max(0, min(position - limit // 4, len(text) - limit))
        end = len(text)
        heading_end = -1
        if entry_starts:
            entry_index = bisect_right(entry_starts, position) - 1
            entry_start = entry_starts[entry_index] if entry_index >= 0 else 0
            if entry_index + 1 < len(entry_starts):
                end = entry_starts[entry_index + 1]
            if entry_index >= 0:
                title_end = text.find(".", entry_start, end)
                heading_end = title_end if title_end >= 0 else entry_start
            # Manter o título sempre que a ocorrência cabe na mesma janela.
            # Entradas maiores continuam sinalizadas como parciais; a leitura
            # da página permanece necessária para verificar todas as condições.
            start = (entry_start if position - entry_start < limit
                     else max(start, entry_start))
        while start and start < len(text) and not text[start - 1].isspace():
            start += 1
        preview = _clip(text[start:end], limit).rstrip()
        hits = [(term, left, right) for term, left, right in matches
                if left >= start and right <= start + len(preview)]
        if not hits:
            continue
        unique_terms = {term for term, _, _ in hits}
        heading_terms = {term for term, _, right in hits if right <= heading_end}
        span = hits[-1][2] - hits[0][1]
        # O nome completo de uma entrada tem mais valor que palavras genéricas
        # como "requisitos" no corpo de outra entrada de nome parecido.
        score = (len(heading_terms), sum(map(len, heading_terms)),
                 len(unique_terms), sum(map(len, unique_terms)), -span, -start)
        if score > best_score:
            best_preview, best_score = preview, score
    return best_preview or _clip(text, limit)


class SourceRegistry:
    """Citações estáveis e evidências deduplicadas durante uma única resposta."""

    def __init__(self, max_sources: int = 12, max_total_chars: int = 16000):
        if (type(max_sources) is not int or max_sources < 1
                or type(max_total_chars) is not int or max_total_chars < 1):
            raise ValueError("Os limites do registro devem ser inteiros positivos.")
        self.max_sources = max_sources
        self.max_total_chars = max_total_chars
        self._sources: list[dict] = []
        self._keys: dict[tuple[str, int], int] = {}
        self._warnings: list[str] = []
        self._total_chars = 0

    @property
    def sources(self) -> list[dict]:
        # O consumidor não deve conseguir renumerar ou alterar evidências antigas.
        return deepcopy(self._sources)

    @property
    def warnings(self) -> list[str]:
        return self._warnings.copy()

    @property
    def total_chars(self) -> int:
        return self._total_chars

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)

    def add(self, source: dict) -> dict | None:
        kind = source.get("source_kind", "chunk")
        chunk_id = source.get("id")
        if kind not in {"chunk", "page", "catalog"} or type(chunk_id) is not int or chunk_id < 1:
            raise ValueError("Fonte sem tipo ou identificador canônico válido.")
        key = (kind, chunk_id)
        if key in self._keys:
            return {**deepcopy(self._sources[self._keys[key]]), "reused": True}
        if len(self._sources) >= self.max_sources:
            self._warn("Limite de fontes atingido; novas evidências não foram adicionadas.")
            return None
        available = self.max_total_chars - self._total_chars
        if available <= 0:
            self._warn("Limite de texto das fontes atingido; novas evidências não foram adicionadas.")
            return None
        text = source.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Fonte sem texto canônico.")
        original_chars = source.get("original_chars", len(text))
        if type(original_chars) is not int or original_chars < len(text):
            original_chars = len(text)
        clipped = _clip(text, available)
        if not clipped:
            self._warn("Não há orçamento de texto suficiente para adicionar outra fonte.")
            return None
        # Allowlist: caminhos de arquivo ou propriedades arbitrárias não são expostos.
        item = {key: source.get(key) for key in (
            "id", "title", "edition", "pdf_page", "printed_page", "chapter", "section",
        )}
        if type(source.get("page_id")) is int:
            item["page_id"] = source["page_id"]
        item.update({
            "citation_id": len(self._sources) + 1,
            "source_kind": kind,
            "text": clipped,
            "methods": [method for method in source.get("methods", [])
                        if isinstance(method, str)],
            "original_chars": original_chars,
            "returned_chars": len(clipped),
            "truncated": bool(source.get("truncated")) or len(clipped) < original_chars,
        })
        self._keys[key] = len(self._sources)
        self._sources.append(item)
        self._total_chars += len(clipped)
        if item["truncated"]:
            self._warn(
                "Uma ou mais fontes tiveram texto cortado pelo limite de contexto; "
                "não trate um trecho parcial como a página inteira."
            )
        return {**deepcopy(item), "reused": False}


class BookTools:
    """Busca e leitura canônica, vinculadas ao livro ativo no início da resposta."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        registry: SourceRegistry | None = None,
        *,
        max_page_chars: int = 6000,
        retriever: Callable | None = None,
    ):
        if type(max_page_chars) is not int or not 1 <= max_page_chars <= 6000:
            raise ValueError("O limite por página deve estar entre 1 e 6000 caracteres.")
        self.conn = conn
        self.settings = settings
        self.registry = registry if registry is not None else SourceRegistry()
        self.max_page_chars = max_page_chars
        self._retrieve = retriever if retriever is not None else retrieve
        book = self._active_book()
        self._book = dict(book) if book is not None else None

    def _active_book(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT id, sha256, title, edition, page_count, imported_at FROM books LIMIT 1"
        ).fetchone()

    def _book_error(self) -> dict | None:
        current = self._active_book()
        if current is None or self._book is None:
            return self._error("book_unavailable", "Não há livro ativo para consultar.")
        if dict(current) != self._book:
            return self._error("book_changed", "O livro ativo mudou. Inicie uma nova consulta.")
        return None

    @staticmethod
    def _error(status: str, message: str) -> dict:
        return {"status": status, "sources": [], "warnings": [message]}

    def _result(self, sources: list[dict], warnings: list[str], **metadata) -> dict:
        return {
            "status": "ok",
            "sources": sources,
            "warnings": list(dict.fromkeys([*warnings, *self.registry.warnings])),
            "book": {key: self._book[key] for key in ("title", "edition", "page_count")},
            **metadata,
        }

    def execute(self, name: str, arguments: object) -> dict:
        """Despacho fechado: nenhum nome de função arbitrário é executado."""
        if not isinstance(name, str) or name not in {"search_book", "read_book_pages"}:
            return self._error("unknown_tool", "Ferramenta não permitida.")
        model = SearchBookArgs if name == "search_book" else ReadBookPagesArgs
        try:
            request = model.model_validate(arguments)
        except ValidationError:
            message = (
                "Use somente query, como texto de 3 a 500 caracteres."
                if name == "search_book" else
                "Use somente pdf_pages, como lista de um ou dois inteiros positivos."
            )
            return self._error("invalid_arguments", message)
        error = self._book_error()
        if error:
            return error
        if name == "search_book":
            return self._search(request.query)
        return self._read_pages(request.pdf_pages)

    def search_book(self, query: str) -> dict:
        return self.execute("search_book", {"query": query})

    def read_book_pages(self, pdf_pages: list[int]) -> dict:
        return self.execute("read_book_pages", {"pdf_pages": pdf_pages})

    def _search(self, query: str) -> dict:
        result = self._retrieve(self.conn, query, self.settings, limit=3)
        # Se houve reimportação durante a recuperação, não misturar referências.
        error = self._book_error()
        if error:
            return error
        sources = []
        warnings = list(result.get("warnings", []))
        for candidate in result.get("sources", [])[:3]:
            chunk_id = candidate.get("id")
            if type(chunk_id) is not int or chunk_id < 1:
                continue
            row = self.conn.execute(
                """SELECT c.id, p.id AS page_id, c.text, p.pdf_page, p.printed_page,
                          p.chapter, p.section, b.title, b.edition
                   FROM chunks c JOIN pages p ON p.id=c.page_id
                   JOIN books b ON b.id=p.book_id
                   WHERE c.id=? AND b.id=?""",
                (chunk_id, self._book["id"]),
            ).fetchone()
            if row is None:
                warnings.append("Um trecho recuperado não está mais disponível no livro ativo.")
                continue
            item = self.registry.add({
                **dict(row), "source_kind": "chunk",
                "text": _search_preview(row["text"], query), "original_chars": len(row["text"]),
                "methods": candidate.get("methods", []),
            })
            if item is not None:
                sources.append(item)
        return self._result(sources, warnings, retrieval=result.get("retrieval", {}))

    def _read_pages(self, pdf_pages: list[int]) -> dict:
        sources = []
        warnings = []
        for number in dict.fromkeys(pdf_pages):
            row = self.conn.execute(
                """SELECT p.id AS page_id, p.text, p.pdf_page, p.printed_page,
                          p.chapter, p.section, b.title, b.edition,
                          (SELECT c.id FROM chunks c WHERE c.page_id=p.id
                           ORDER BY c.position, c.id LIMIT 1) AS id
                   FROM pages p JOIN books b ON b.id=p.book_id
                   WHERE p.book_id=? AND p.pdf_page=?""",
                (self._book["id"], number),
            ).fetchone()
            if row is None:
                warnings.append(f"Página física {number} não encontrada no livro ativo.")
                continue
            if row["id"] is None or not any(char.isalpha() for char in row["text"]):
                warnings.append(
                    f"Página física {number} não contém texto consultável suficiente; "
                    "confira o PDF original."
                )
                continue
            full_text = row["text"]
            item = self.registry.add({
                **dict(row), "source_kind": "page", "methods": ["leitura da página"],
                "text": _clip(full_text, self.max_page_chars),
                "original_chars": len(full_text),
                "truncated": len(full_text) > self.max_page_chars,
            })
            if item is not None:
                sources.append(item)
        return self._result(sources, warnings, requested_pdf_pages=list(dict.fromkeys(pdf_pages)))
