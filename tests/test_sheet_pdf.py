"""Formulário sintético: o template privado não é necessário para os testes."""
from pathlib import Path
import re

import pymupdf
import pytest

from rpg_assistente.sheet_pdf import SheetPdfError, export_sheet_pdf, sheet_pdf_info


@pytest.fixture
def template(tmp_path):
    # A geometria mínima replica somente os pontos de controle do adaptador,
    # sem copiar arte, textos ou o PDF particular para o repositório.
    anchors = {
        1: (0, (76.32, 33.12, 232.56, 48.24)),
        13: (0, (188.64, 108, 213.12, 126)),
        129: (0, (138.96, 345.60, 322.56, 360.72)),
        166: (1, (29.52, 47.52, 184.32, 60.48)),
        247: (1, (321.84, 371.52, 500.40, 389.52)),
    }
    doc = pymupdf.open()
    doc.new_page(width=581.1, height=779.53)
    doc.new_page(width=581.1, height=779.53)
    for number in (1, 2, *range(11, 341)):
        page_index, rect = anchors.get(number, (int(number >= 165), (20, 20, 120, 40)))
        page = doc[page_index]
        widget = pymupdf.Widget()
        widget.field_name = f"untitled{number}"
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(rect)
        widget.field_value = "VALOR ANTIGO"
        page.add_widget(widget)
    path = tmp_path / "form.pdf"
    doc.save(path)
    doc.close()
    return path


def fields(document):
    return {widget.field_name: widget.field_value for page in document for widget in page.widgets() or []}


def test_info_without_leaking_private_path(template):
    assert sheet_pdf_info(template)["available"]
    assert not sheet_pdf_info(None)["available"]
    missing = sheet_pdf_info(Path("C:/private/missing-template.pdf"))
    assert not missing["available"]
    assert "private" not in str(missing)


def test_export_preserves_template_values_tree_and_appearances(template):
    original = template.read_bytes()
    payload = {"name": "João", "character_class": "combatente", "nex": 15,
               "attributes": {"agilidade": 2, "forca": 3, "intelecto": 1, "presenca": 0, "vigor": 3},
               "derived": {"pv": 40, "pe": 0}, "skills": [{"name": "Luta", "bonus": 5}]}
    result = export_sheet_pdf(payload, template)
    assert template.read_bytes() == original
    with pymupdf.open(stream=result, filetype="pdf") as doc:
        actual = fields(doc)
        assert len(actual) == 332
        assert actual["untitled1"] == "João"
        assert actual["untitled2"] == ""  # clear source's previous player
        assert actual["untitled11"] == "0"
        assert actual["untitled134"] == "40"
        assert actual["untitled136"] == "0"
        assert actual["untitled141"] == ""  # no invented SAN value
        assert actual["untitled60"] == "5"
        assert "VALORES PARCIAIS" in doc[0].get_text()
        assert "VALORES PARCIAIS" in doc[1].get_text()
        assert "Conferência antes de jogar" in doc[2].get_text()
        roots = {int(value) for value in re.findall(r"(\d+) 0 R", doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")[1])}
        widgets = [widget for page in doc for widget in page.widgets() or []]
        assert roots == {widget.xref for widget in widgets}
        assert all(doc.xref_get_key(widget.xref, "AP/N")[0] == "xref" for widget in widgets)


def test_long_text_and_extra_rows_are_preserved_in_complement(template):
    description = "Descrição extensa para confirmar preservação. " * 30
    result = export_sheet_pdf({"name": "Teste", "powers": [
        {"name": f"Poder {index}", "description": description if index == 0 else f"Efeito {index}"}
        for index in range(22)
    ], "equipment": [{"name": f"Item {index}", "quantity": 1, "spaces": 2} for index in range(24)],
        "notes": "Anotação importante."}, template)
    with pymupdf.open(stream=result, filetype="pdf") as doc:
        assert len(doc) > 2
        full_text = " ".join(" ".join(page.get_text().split()) for page in doc)
        assert "Poder 21: Efeito 21" in full_text
        assert "Item 23" in full_text
        assert description.strip() in full_text
        assert "Anotação importante." in full_text
        assert "Ver complemento" in full_text


def test_rejects_changed_layout(template):
    with pymupdf.open(template) as doc:
        page = doc[0]
        widget = next(page.widgets())
        widget.rect = pymupdf.Rect(0, 0, 100, 30)
        widget.update()
        changed = doc.tobytes()
    template.write_bytes(changed)
    assert not sheet_pdf_info(template)["available"]
    with pytest.raises(SheetPdfError, match="layout"):
        export_sheet_pdf({}, template)


def test_rejects_inconsistent_canonical_field_tree(template):
    with pymupdf.open(template) as doc:
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/Fields", "[]")
        altered = doc.tobytes()
    template.write_bytes(altered)
    with pytest.raises(SheetPdfError, match="inconsistentes"):
        export_sheet_pdf({}, template)


def test_rejects_unsupported_characters_instead_of_silent_glyph_loss(template):
    with pytest.raises(SheetPdfError, match="emojis"):
        export_sheet_pdf({"name": "Agente 🔥"}, template)


def test_common_math_and_arrows_are_exported_with_equivalent_notation(template):
    result = export_sheet_pdf({"name":"Agente", "notes":"Força 3 → 15 espaços; requisito ≥ 2; penalidade −1d20."},template)
    with pymupdf.open(stream=result,filetype="pdf") as doc:
        text = " ".join(page.get_text() for page in doc)
        assert "3 -> 15" in text
        assert ">= 2" in text
        assert "-1d20" in text
