"""Regras determinísticas para uma ficha básica de Ordem Paranormal RPG 1.1.

Recorte: criação em NEX 5%, atributos e valores derivados *base*. Benefícios de
origem, perícias, equipamentos e poderes ainda exigem revisão separada. Uma
validação bem-sucedida deste módulo nunca significa que a ficha inteira passou.

As referências abaixo foram conferidas no PDF original, inclusive visualmente
nas páginas de características das três classes. Não há consulta a uma LLM.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError


RULESET_ID = "ordem-paranormal-1.1-nex5"
BOOK_TITLE = "Ordem Paranormal RPG"

ATTRIBUTE_LABELS = {
    "agilidade": "Agilidade",
    "forca": "Força",
    "intelecto": "Intelecto",
    "presenca": "Presença",
    "vigor": "Vigor",
}

CLASS_RULES = {
    "combatente": {"label": "Combatente", "pv": 20, "pe": 2, "san": 12, "pdf_page": 33},
    "especialista": {"label": "Especialista", "pv": 16, "pe": 3, "san": 16, "pdf_page": 37},
    "ocultista": {"label": "Ocultista", "pv": 12, "pe": 4, "san": 20, "pdf_page": 41},
}

SCOPE = {
    "ruleset_id": RULESET_ID,
    "system": BOOK_TITLE,
    "edition": "1.1",
    "nex": 5,
    "full_sheet_validated": False,
    "description": (
        "Validação parcial da criação em NEX 5%: distribuição dos cinco atributos, "
        "classe e cálculo dos valores iniciais base."
    ),
    "excluded": [
        "Benefícios e seleção de origem",
        "Perícias e escolhas das habilidades de classe",
        "Poderes, rituais e seus pré-requisitos",
        "Equipamento, ataques e modificadores de Defesa",
        "Evolução de NEX, trilhas e regras da mesa",
    ],
}


class Attributes(BaseModel):
    """Tipos são verificados aqui; limites de jogo viram erros de domínio."""

    model_config = ConfigDict(extra="forbid")

    agilidade: StrictInt
    forca: StrictInt
    intelecto: StrictInt
    presenca: StrictInt
    vigor: StrictInt


class CharacterInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80, strict=True)
    character_class: Literal["combatente", "especialista", "ocultista"]
    attributes: Attributes
    nex: StrictInt = 5
    origin: str = Field(default="", max_length=120, strict=True)
    notes: str = Field(default="", max_length=2000, strict=True)


def _reference(pdf_page: int, section: str) -> dict:
    # Estes rótulos foram conferidos no PDF 1.1; não são um offset genérico
    # aplicável a PDFs arbitrários.
    labels = {22: "14", 31: "23", 33: "25", 37: "29", 41: "33", 44: "36"}
    return {
        "title": BOOK_TITLE,
        "edition": "1.1",
        "pdf_page": pdf_page,
        "printed_page": labels[pdf_page],
        "section": section,
    }


def _sources(character_class: str | None = None) -> list[dict]:
    refs = [
        _reference(22, "Atributos — Geração de atributos"),
        _reference(31, "Exposição Paranormal — Benefícios por NEX"),
    ]
    classes = [character_class] if character_class in CLASS_RULES else CLASS_RULES
    refs.extend(
        _reference(CLASS_RULES[key]["pdf_page"], f"{CLASS_RULES[key]['label']} — Características")
        for key in classes
    )
    refs.append(_reference(44, "Toques finais — Características derivadas / Defesa"))
    return refs


def character_options() -> dict:
    """Opções e limites que a interface pode apresentar sem repetir regras."""

    return {
        "classes": [{"value": key, "label": value["label"]} for key, value in CLASS_RULES.items()],
        "attributes": [{"key": key, "label": label} for key, label in ATTRIBUTE_LABELS.items()],
        "nex_values": [5],
        "attribute_rules": {
            "base": 1, "points": 4, "min": 0, "max": 3, "max_reduced": 1, "total": 9,
        },
        "scope": deepcopy(SCOPE),
        "sources": _sources(),
    }


def _input_errors(exc: ValidationError) -> list[dict]:
    """Não inclui o valor recebido nem detalhes internos no relatório."""

    messages = {
        "missing": "Campo obrigatório.",
        "int_type": "Use um número inteiro, sem texto, frações ou booleanos.",
        "string_type": "Use texto neste campo.",
        "string_too_short": "Preencha este campo.",
        "string_too_long": "O texto excede o limite permitido para este campo.",
        "literal_error": "Escolha combatente, especialista ou ocultista.",
        "extra_forbidden": "Este campo não é aceito no modelo de ficha básica.",
    }
    return [
        {
            "field": ".".join(str(part) for part in error["loc"]) or "character",
            "message": messages.get(error["type"], "Formato inválido para este campo."),
        }
        for error in exc.errors(include_url=False, include_input=False)
    ]


def validate_character(payload: CharacterInput | dict) -> dict:
    """Valida o recorte suportado e calcula bases apenas quando ele é válido.

    ``valid`` refere-se exclusivamente ao ``scope`` devolvido. Todos os números
    derivados têm sufixo ``_base`` para não ocultar modificadores pendentes.
    """

    report = {
        "valid": False,
        "errors": [],
        "warnings": [
            "Validação parcial: origem, perícias, poderes, rituais e equipamento precisam de revisão.",
            "PV, PE e SAN abaixo são bases da classe; poderes de origem podem alterar esses valores.",
            "A Defesa base não inclui habilidades, equipamentos ou condições.",
            "O limite base de PE por turno não impede usar uma habilidade em seu custo mínimo, "
            "conforme a exceção do livro (página impressa 23).",
        ],
        "derived": None,
        "sources": _sources(),
        "scope": deepcopy(SCOPE),
    }
    try:
        data = payload.model_dump() if isinstance(payload, CharacterInput) else payload
        character = CharacterInput.model_validate(data)
    except ValidationError as exc:
        report["errors"] = _input_errors(exc)
        return report

    report["sources"] = _sources(character.character_class)
    errors = report["errors"]
    if character.nex != 5:
        errors.append({
            "field": "nex",
            "message": "Este validador suporta apenas a criação em NEX 5%; não valida evolução.",
        })

    attributes = character.attributes.model_dump()
    for key, value in attributes.items():
        if not 0 <= value <= 3:
            errors.append({
                "field": f"attributes.{key}",
                "message": f"{ATTRIBUTE_LABELS[key]} deve estar entre 0 e 3 na criação.",
            })
    if sum(value == 0 for value in attributes.values()) > 1:
        errors.append({
            "field": "attributes",
            "message": "A criação permite reduzir no máximo um atributo para 0.",
        })
    total = sum(attributes.values())
    if total != 9:
        errors.append({
            "field": "attributes",
            "message": (
                f"A soma dos atributos deve ser 9; a soma informada é {total}. "
                "Comece com 1 em cada atributo e distribua 4 pontos. Reduzir um atributo "
                "para 0 libera mais 1 ponto para redistribuir, mantendo essa soma."
            ),
        })

    if character.origin:
        report["warnings"].append(
            "A origem foi guardada apenas como anotação. Seus benefícios não foram aplicados "
            "aos valores base nem validados."
        )
    if character.notes:
        report["warnings"].append(
            "As anotações foram guardadas como texto; não alteram cálculos e não são interpretadas como regras."
        )
    if character.character_class == "ocultista":
        report["warnings"].append("A escolha e os pré-requisitos dos rituais do ocultista ainda não são validados.")

    if errors:
        return report

    rule = CLASS_RULES[character.character_class]
    report["valid"] = True
    report["derived"] = {
        "pv_base": rule["pv"] + character.attributes.vigor,
        "pe_base": rule["pe"] + character.attributes.presenca,
        "san_base": rule["san"],
        "defesa_base": 10 + character.attributes.agilidade,
        "limite_pe_base": 1,
        "attribute_total": total,
    }
    return report
