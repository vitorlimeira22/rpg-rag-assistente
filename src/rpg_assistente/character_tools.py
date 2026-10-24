"""Ferramenta de leitura: cálculos de criação já implementados em Python.

Não salva fichas, não escolhe atributos e não aplica modificadores. O perfil
exige as páginas e os blocos de regras da edição cuja criação foi conferida.
Os recortes são substrings do SQLite canônico, não texto escrito pela LLM.
"""
from __future__ import annotations

from copy import deepcopy
import re
import sqlite3
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError

from .characters import Attributes, CharacterInput, CLASS_RULES, character_options, validate_character


class ValidateBasicCharacterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    character_class: Literal["combatente", "especialista", "ocultista"]
    nex: StrictInt
    attributes: Attributes


CHARACTER_TOOL_SCHEMAS = [{
    "type": "function",
    "function": {
        "name": "validate_basic_character",
        "description": (
            "Valida em Python a distribuição dos cinco atributos e calcula PV, PE, SAN, "
            "Defesa e limite de PE BASES na criação em NEX 5%, Ordem Paranormal 1.1. "
            "Use somente classe, NEX e TODOS os atributos informados pelo usuário; "
            "se faltarem dados, pergunte e não invente valores para chamar a ferramenta. "
            "Não salva dados nem certifica uma ficha completa: não valida origem, perícias, "
            "rituais, poderes, equipamentos ou evolução acima de NEX 5%. "
            "Resultados dependem dos argumentos apresentados e não incluem modificadores."
        ),
        "parameters": ValidateBasicCharacterArgs.model_json_schema(),
    },
}]


_BLOCKS = {
    22: r"GERAÇÃO\s+DE ATRIBUTOS\b.*?(?=\nEXEMPLO\b)",
    31: r"Limite de PE\..*?(?=\nTABELA\b)",
    33: r"PONTOS DE VIDA INICIAIS\b.*?\nCARACTERÍSTICAS\b",
    37: r"PONTOS DE VIDA INICIAIS\b.*?\nCARACTERÍSTICAS\b",
    41: r"PONTOS DE VIDA INICIAIS\b.*?\nCARACTERÍSTICAS\b",
    44: r"(?:^|\n)Defesa\s*\n.*?(?=\nDeslocamento\b)",
}


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"\w+", "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(c)
    )))


def _book(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT id,title,edition,sha256 FROM books ORDER BY id LIMIT 1").fetchone()
    return dict(row) if row else None


class CharacterTools:
    """Perfil vinculado ao livro atual; execute nunca escreve no banco."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._book = _book(conn)
        self._evidence: dict[int, dict] = {}
        self.available = self._load_profile()

    def _load_profile(self) -> bool:
        if (not self._book or "ordem paranormal" not in _normalized(self._book["title"])
                or _normalized(self._book["edition"]) not in {"1 1", "v1 1"}):
            return False
        for reference in character_options()["sources"]:
            page = reference["pdf_page"]
            row = self.conn.execute(
                "SELECT p.id AS page_id,p.pdf_page,p.printed_page,p.chapter,p.section,p.text, "
                "(SELECT c.id FROM chunks c WHERE c.page_id=p.id ORDER BY c.position,c.id LIMIT 1) AS id "
                "FROM pages p WHERE p.book_id=? AND p.pdf_page=?",
                (self._book["id"], page),
            ).fetchone()
            if row is None or row["printed_page"] != reference["printed_page"] or row["id"] is None:
                return False
            match = re.search(_BLOCKS[page], row["text"], re.IGNORECASE | re.DOTALL)
            if not match or len(match.group().strip()) > 2600:
                return False
            self._evidence[page] = {
                "id": row["id"], "page_id": row["page_id"],
                "title": self._book["title"], "edition": self._book["edition"],
                "pdf_page": page, "printed_page": row["printed_page"],
                "chapter": row["chapter"], "section": reference["section"],
                "text": match.group().strip(), "label": reference["section"],
            }
        return True

    def execute(self, name: str, arguments: dict) -> dict:
        if name != "validate_basic_character":
            return {"status": "invalid", "warnings": ["Ferramenta de ficha não reconhecida."]}
        if not self.available:
            return {"status": "unavailable", "warnings": [
                "O livro ativo não corresponde às páginas de regras conferidas para Ordem Paranormal 1.1."
            ]}
        if self._book != _book(self.conn):
            return {"status": "book_changed", "warnings": [
                "O livro ativo mudou durante a consulta; nenhum cálculo foi executado."
            ]}
        try:
            args = ValidateBasicCharacterArgs.model_validate(arguments)
        except ValidationError:
            return {"status": "invalid", "warnings": [
                "Informe classe, NEX inteiro e os cinco atributos inteiros, sem campos adicionais. "
                "Peça os dados que faltarem; não escolha valores pelo usuário."
            ]}
        data = args.model_dump()
        validation = validate_character(CharacterInput(name="Ficha em revisão", **data))
        pages = [22, 31, CLASS_RULES[args.character_class]["pdf_page"], 44]
        return {
            "status": "ok" if validation["valid"] else "invalid_character",
            "arguments": data, "validation": validation,
            "rule_pdf_pages": pages,
            "rule_evidence": [deepcopy(self._evidence[page]) for page in pages],
            "book_sha256": self._book["sha256"],
            "warnings": [
                "Cálculo parcial em Python para os argumentos exibidos; não é certificação da ficha completa. "
                "Os valores são bases, sem aplicar origem, poderes, equipamentos ou condições."
            ],
        }

    def creation_rules(self, sources: list[dict]) -> dict:
        """Distingue bases iniciais de ganhos por NEX em fontes de classe.

        Não interpreta a pergunta nem assume atributos. Os fatos estruturados
        são regras do perfil; ``rule_evidence`` permanece texto canônico.
        """
        result = {"facts": [], "rule_evidence": []}
        if not self.available or self._book != _book(self.conn):
            return result
        pages = {
            source.get("pdf_page") for source in sources
            if isinstance(source, dict) and isinstance(source.get("text"), str)
            and type(source.get("pdf_page")) is int
            and "pontos de vida iniciais" in _normalized(source["text"])
        }
        for character_class, rule in CLASS_RULES.items():
            page = rule["pdf_page"]
            if page not in pages:
                continue
            result["facts"].append({
                "character_class": character_class, "nex": 5, "pdf_page": page,
                "pv_base_formula": f"{rule['pv']} + Vigor",
                "pe_base_formula": f"{rule['pe']} + Presença",
                "san_base": rule["san"],
                "scope": (
                    "Bases da classe na criação em NEX 5%, sem aplicar atributos ou modificadores. "
                    "Não são os ganhos a cada avanço de NEX nem validação de ficha completa."
                ),
            })
            result["rule_evidence"].append(deepcopy(self._evidence[page]))
        return result
