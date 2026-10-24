"""Exportação local para a ficha editável fornecida pelo usuário.

O PDF não faz parte do pacote. O mapeamento é específico desse formulário e
valida sua estrutura antes de escrever. Valores ausentes permanecem em branco;
este módulo não calcula nem certifica regras. Textos extensos e linhas extras
vão para um complemento, preservando os campos originais editáveis.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import unicodedata

import pymupdf


_FIELD_NAMES = {f"untitled{i}" for i in (1, 2, *range(11, 341))}
_ANCHORS = {
    "untitled1": (0, (76.32, 33.12, 232.56, 48.24)),
    "untitled13": (0, (188.64, 108, 213.12, 126)),
    "untitled129": (0, (138.96, 345.60, 322.56, 360.72)),
    "untitled166": (1, (29.52, 47.52, 184.32, 60.48)),
    "untitled247": (1, (321.84, 371.52, 500.40, 389.52)),
}
_ATTR_FIELDS = {"agilidade": 13, "forca": 12, "intelecto": 14, "presenca": 11, "vigor": 15}
_PARTIAL_NOTICE = "VALORES PARCIAIS - Benefícios e exceções podem alterar os totais. Confira o complemento antes de jogar."
_SKILLS = [
    ("Acrobacia", "agilidade"), ("Adestramento", "presenca"), ("Artes", "presenca"),
    ("Atletismo", "forca"), ("Atualidades", "intelecto"), ("Ciências", "intelecto"),
    ("Crime", "agilidade"), ("Diplomacia", "presenca"), ("Enganação", "presenca"),
    ("Fortitude", "vigor"), ("Furtividade", "agilidade"), ("Iniciativa", "agilidade"),
    ("Intimidação", "presenca"), ("Intuição", "presenca"), ("Investigação", "intelecto"),
    ("Luta", "forca"), ("Medicina", "intelecto"), ("Ocultismo", "intelecto"),
    ("Percepção", "presenca"), ("Pilotagem", "agilidade"), ("Pontaria", "agilidade"),
    ("Profissão", "intelecto"), ("Reflexos", "agilidade"), ("Religião", "presenca"),
    ("Sobrevivência", "intelecto"), ("Tática", "intelecto"), ("Tecnologia", "intelecto"),
    ("Vontade", "presenca"),
]


class SheetPdfError(ValueError):
    """Erro apresentável ao usuário, sem expor caminhos locais."""


def _normalized(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def _refs(value: str) -> set[int]:
    return {int(number) for number in re.findall(r"(\d+)\s+\d+\s+R", value)}


def _inspect(document: pymupdf.Document) -> dict:
    if document.needs_pass or len(document) != 2:
        raise SheetPdfError("O modelo deve ser a ficha editável de duas páginas, sem senha.")
    widgets = [(page.number, widget) for page in document for widget in page.widgets() or []]
    counts = Counter(widget.field_name for _, widget in widgets)
    if set(counts) != _FIELD_NAMES or any(count != 1 for count in counts.values()):
        raise SheetPdfError("O PDF não corresponde ao modelo de ficha compatível (campos ausentes ou duplicados).")
    by_name = {widget.field_name: (page, widget) for page, widget in widgets}
    for name, (expected_page, rect) in _ANCHORS.items():
        page, widget = by_name[name]
        if page != expected_page or any(abs(a - b) > 1 for a, b in zip(widget.rect, rect)):
            raise SheetPdfError("O layout do modelo foi alterado; o mapeamento dos campos precisa ser revisado.")
    kind, _ = document.xref_get_key(document.pdf_catalog(), "AcroForm")
    if kind not in {"xref", "dict"}:
        raise SheetPdfError("O PDF não possui uma árvore de campos editáveis compatível.")
    canonical = _refs(document.xref_get_key(document.pdf_catalog(), "AcroForm/Fields")[1])
    # Este modelo usa widgets terminais no próprio /Fields. Não tentar reparar
    # árvores ambíguas nem duplicar campos silenciosamente.
    if canonical != {widget.xref for _, widget in widgets}:
        raise SheetPdfError("Os campos do PDF estão inconsistentes com suas anotações visuais.")
    if any(widget.field_type != pymupdf.PDF_WIDGET_TYPE_TEXT for _, widget in widgets):
        raise SheetPdfError("O modelo contém tipos de campo não suportados.")
    return by_name


def sheet_pdf_info(template_path: Path | None) -> dict:
    """Estado público, sem revelar o caminho do arquivo privado."""
    result = {
        "available": False, "reason": "Configure o caminho local do modelo de ficha PDF.",
        "editable": True,
        "supported_fields": ["Identificação", "Atributos", "Valores derivados informados", "Perícias",
                             "Habilidades e rituais", "Inventário", "Anotações"],
        "limits": {"ability_rows": 20, "inventory_rows": 22},
        "notes": ["Textos extensos e linhas excedentes aparecem em páginas de complemento.",
                  "Valores parciais: benefícios e exceções podem exigir ajuste dos totais.",
                  "A exportação não certifica regras; valores ausentes ficam em branco."],
    }
    if template_path is None:
        return result
    try:
        with pymupdf.open(Path(template_path)) as document:
            _inspect(document)
        result.update(available=True, reason=None)
    except SheetPdfError as exc:
        result["reason"] = str(exc)
    except (OSError, RuntimeError, ValueError):
        result["reason"] = "Não foi possível abrir o modelo de ficha PDF configurado."
    return result


def _text(value) -> str:
    if value is None:
        return ""
    # Mantém caracteres portugueses, evita controles e fontes sem glifos.
    text = " ".join(str(value).split())
    text = text.replace("–", "-").replace("—", "-").replace("’", "'").replace("“", '"').replace("”", '"')
    for symbol, replacement in {"→":"->", "←":"<-", "↔":"<->", "−":"-", "‑":"-", "‐":"-",
                                "≥":">=", "≤":"<=", "≠":"!=", "…":"..."}.items():
        text = text.replace(symbol, replacement)
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise SheetPdfError("O modelo PDF aceita texto latino. Remova emojis ou caracteres sem suporte antes de exportar.") from exc
    return text


def _mapping(data: dict) -> tuple[dict[str, str], list[tuple[str, str]]]:
    values: dict[str, str] = {}
    extra: list[tuple[str, str]] = [(
        "Conferência antes de jogar",
        "Os valores de PV, PE, SAN, Defesa e limite de PE foram fornecidos pela aplicação "
        "dentro de um escopo de cálculo parcial. Benefícios de origem, trilha, poderes, "
        "rituais e outras exceções podem alterar esses totais. Confira as pendências no "
        "editor e as fontes do livro. Esta exportação não aprova uma ficha completa.",
    )]

    def put(number, value):
        values[f"untitled{number}"] = _text(value)

    attrs = data.get("attributes") or {}
    derived = data.get("derived") or {}
    put(1, data.get("name"))
    put(2, data.get("player"))
    put(129, data.get("origin"))
    put(130, " / ".join(filter(None, [_text(data.get("character_class")).capitalize(), _text(data.get("track"))])))
    put(131, data.get("nex"))
    put(247, data.get("rank"))
    for key, number in _ATTR_FIELDS.items():
        put(number, attrs.get(key))
    for number, key, alias in [(132, "pe_per_turn", "limite_pe_base"), (134, "pv", "pv_base"),
                               (136, "pe", "pe_base"), (138, "defense", "defesa_base"), (141, "san", "san_base")]:
        put(number, derived.get(key, derived.get(alias)))
    for number, key in [(133, "movement"), (135, "pv_current"), (137, "pe_current"),
                        (139, "defense_equipment"), (140, "defense_other"), (142, "san_current"),
                        (165, "ritual_dc"), (253, "load_max"), (254, "load_current")]:
        put(number, derived.get(key))
    put(253, derived.get("capacity", derived.get("load_max")))
    put(254, derived.get("load", derived.get("load_current")))
    put(143, data.get("protection"))
    put(144, data.get("resistances"))
    put(246, data.get("prestige"))
    put(252, data.get("credit_limit"))
    skills = { _normalized(_text(item.get("name"))): item for item in data.get("skills", []) }
    for index, (name, attribute) in enumerate(_SKILLS):
        skill = skills.pop(_normalized(name), None)
        put(16 + index, attrs.get(attribute))
        if skill:
            put(45 + index, skill.get("bonus"))
            put(73 + index, skill.get("training_bonus", skill.get("bonus")))
            put(101 + index, skill.get("other_bonus"))
            if skill.get("notes"):
                extra.append((f"Perícia - {name}", _text(skill["notes"])))
    for skill in skills.values():
        extra.append(("Perícia adicional", "; ".join(f"{key}: {_text(value)}" for key, value in skill.items())))
    powers = [*(data.get("powers") or []), *(data.get("rituals") or [])]
    for index, power in enumerate(powers):
        if index >= 20:
            details = [_text(power.get("description"))]
            details.extend(f"{label}: {_text(power[key])}" for key, label in [("cost", "Custo"), ("page", "Página")]
                           if power.get(key) is not None)
            extra.append((_text(power.get("name")) or "Habilidade / ritual", "; ".join(filter(None, details))))
            continue
        put(166 if index == 0 else 169 + index, power.get("name"))
        put(167 if index == 0 else 188 + index, power.get("cost"))
        put(168 if index == 0 else 207 + index, power.get("page"))
        put(169 if index == 0 else 226 + index, power.get("description"))
    left_names = [256, 255, *range(264, 273)]
    right_names = [259, *range(291, 301)]
    for index, item in enumerate(data.get("equipment") or []):
        quantity = item.get("quantity")
        name = _text(item.get("name")) + (f" (x{quantity})" if quantity is not None else "")
        if index >= 22:
            extra.append(("Equipamento", "; ".join(f"{key}: {_text(value)}" for key, value in item.items())))
            continue
        row = index % 11
        numbers = (
            (left_names[row], [257, 262, *range(273, 282)][row], [258, 263, *range(282, 291)][row])
            if index < 11 else
            (right_names[row], [260, *range(301, 311)][row], [261, *range(311, 321)][row])
        )
        put(numbers[0], name)
        put(numbers[1], item.get("category"))
        # O editor guarda espaços por unidade; o PDF não tem coluna de
        # quantidade, que já aparece no nome. Exibir o total da linha.
        spaces = item.get("spaces")
        put(numbers[2], spaces * quantity if isinstance(spaces, (int, float)) and isinstance(quantity, int) else spaces)
        if item.get("description"):
            extra.append((name, _text(item["description"])))
    for key, label in [("notes", "Anotações"), ("appearance", "Aparência"),
                       ("personality", "Personalidade"), ("history", "Histórico"), ("objective", "Objetivo")]:
        if data.get(key):
            extra.append((label, _text(data[key])))
    return values, extra


def _lines(text: str, width: float, fontsize: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        if pymupdf.get_text_length(f"{line} {word}".strip(), fontsize=fontsize) <= width:
            line = f"{line} {word}".strip()
            continue
        if line:
            lines.append(line)
        line = ""
        for char in word:
            if pymupdf.get_text_length(line + char, fontsize=fontsize) > width:
                lines.append(line)
                line = ""
            line += char
    if line:
        lines.append(line)
    return lines


def _append_details(document, data: dict, extra: list[tuple[str, str]]) -> None:
    if not extra:
        return
    page, y = None, 800
    for label, value in extra:
        for line_index, line in enumerate(_lines(f"{label}: {value}", 495, 10)):
            if y > 775:
                page = document.new_page(width=595, height=842)
                page.insert_text((50, 40), "FICHA DE AGENTE - COMPLEMENTO", fontsize=14, fontname="hebo")
                page.insert_text((50, 60), _text(data.get("name"))[:70], fontsize=10)
                page.insert_text((50, 80), "Detalhes registrados. A exportação não certifica as regras da ficha.", fontsize=9)
                y = 108
            page.insert_text((50, y), line, fontsize=10)
            y += 14
        y += 8


def export_sheet_pdf(sheet_data: dict, template_path: Path) -> bytes:
    """Gera bytes de uma cópia preenchida, preservando o arquivo de origem."""
    try:
        document = pymupdf.open(Path(template_path))
    except (OSError, RuntimeError, ValueError) as exc:
        raise SheetPdfError("Não foi possível abrir o modelo de ficha PDF configurado.") from exc
    with document:
        _inspect(document)
        values, extra = _mapping(sheet_data)
        for page in document:
            page.insert_text((20, 11), _PARTIAL_NOTICE, fontsize=6.5)
            for widget in page.widgets() or []:
                value = values.get(widget.field_name, "")
                size = min(12, max(7, widget.rect.height * .65))
                if widget.field_name in {f"untitled{i}" for i in _ATTR_FIELDS.values()}:
                    size = 16
                width = widget.rect.width - 4
                if value:
                    size = min(size, width / max(pymupdf.get_text_length(value, fontname="helv", fontsize=1), 1))
                    if size < 7:
                        index = len(extra) + 1
                        extra.append((f"Texto completo da ficha [{index}]", value))
                        value = f"Ver complemento [{index}]"
                        size = min(9, width / max(pymupdf.get_text_length(value, fontsize=1), 1))
                        if size < 6:
                            value, size = f"[{index}]", 7
                # PyMuPDF mantém /V anterior ao receber "" em alguns PDFs.
                # Um espaço gera uma aparência vazia; depois gravamos /V vazio
                # explicitamente. Assim não vaza o jogador padrão do modelo.
                widget.field_value = value or " "
                widget.text_font = "Helv"
                widget.text_fontsize = size
                widget.text_color = (0, 0, 0)
                if widget.field_name in {f"untitled{i}" for i in range(16, 44)}:
                    # O formulário original sobrepõe os campos à sigla impressa
                    # do atributo. Fundo branco evita número e sigla misturados.
                    widget.fill_color = (1, 1, 1)
                widget.update()
                if not value:
                    document.xref_set_key(widget.xref, "V", "()")
                document.xref_set_key(widget.xref, "DV", "null")
                values[widget.field_name] = value
        _append_details(document, sheet_data, extra)
        result = document.tobytes(garbage=3, deflate=True)
    # Verificação lógica e de aparência, independente do visualizador do usuário.
    with pymupdf.open(stream=result, filetype="pdf") as check:
        widgets = [widget for page in check for widget in page.widgets() or []]
        if len(widgets) != len(_FIELD_NAMES):
            raise SheetPdfError("Falha ao preservar os campos editáveis da ficha.")
        for widget in widgets:
            if widget.field_value != values[widget.field_name]:
                raise SheetPdfError("Falha ao gravar os valores nos campos do PDF.")
            if values[widget.field_name] and check.xref_get_key(widget.xref, "AP/N")[0] != "xref":
                raise SheetPdfError("Falha ao atualizar a aparência de um campo do PDF.")
    return result
