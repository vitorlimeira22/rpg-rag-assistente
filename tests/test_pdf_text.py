from types import SimpleNamespace

import pymupdf
import pytest

from rpg_assistente.pdf_text import extract_page_text, normalize_dice_spans


def span(text, font="WarnockPro-Regular"):
    return {"text": text, "font": font}


@pytest.mark.parametrize(("glyph", "expected"), [("O", "1d20"), ("OO", "2d20"), ("OOO", "3d20")])
def test_verified_font_converts_glyph_runs(glyph, expected):
    assert normalize_dice_spans([span(glyph, "d20-Regular")]) == expected


def test_numeric_prefix_signs_and_split_spans_keep_dice_counts():
    spans = [span("Teste 3"), span("O", "d20-Regular"), span("+5; penalidade –"),
             span("O", "d20-Regular"), span("O", "d20-Regular"), span("; bônus +"),
             span("O ", "d20-Regular")]
    assert normalize_dice_spans(spans) == (
        "Teste 3d20+5; penalidade –2d20 [na quantidade de dados]; "
        "bônus +1d20 [na quantidade de dados] "
    )


def test_penalty_icons_keep_pool_meaning_and_do_not_change_numeric_creature_notation():
    assert normalize_dice_spans([span("– "), span("OO", "d20-Regular")]) == (
        "– 2d20 [na quantidade de dados]"
    )
    assert normalize_dice_spans([span("–2"), span("O", "d20-Regular")]) == "–2d20"


def test_only_verified_glyphs_and_font_are_changed():
    spans = [span("O Outro Lado: 2d4, 1d6, d8, d10, 1d12, d20. "),
             span("O", "UnknownDiceFont"), span("/"), span("0?", "d20-Regular")]
    assert normalize_dice_spans(spans) == "O Outro Lado: 2d4, 1d6, d8, d10, 1d12, d20. O/0?"


def test_pdf_subset_font_name_is_supported():
    assert normalize_dice_spans([span("O", "ABCDEF+d20-Regular")]) == "1d20"


def fake_page(raw, lines):
    text_page = SimpleNamespace(
        extractText=lambda **kwargs: raw,
        extractDICT=lambda **kwargs: {"blocks": [{"type": 0, "lines": [{"spans": s} for s in lines]}]},
    )
    return SimpleNamespace(get_textpage=lambda **kwargs: text_page)


def test_extraction_preserves_layout_and_duplicate_lines_with_different_fonts():
    page = fake_page("O\nO\n\nteste\x00continua\n", [
        [span("O")], [span("O", "d20-Regular")], [span("teste\x00")], [span("continua")],
    ])
    assert extract_page_text(page) == "O\n1d20\n\nteste\x00continua\n"


def test_extraction_fails_instead_of_replacing_an_ambiguous_line():
    with pytest.raises(ValueError, match="alinhar"):
        extract_page_text(fake_page("different", [[span("O", "d20-Regular")]]))


def test_real_pdf_text_and_page_order_are_unchanged_without_dice_font():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((300, 60), "O Outro Lado 2d4 e 1d12")
        page.insert_text((60, 60), "Primeira coluna 3d20+5")
        assert extract_page_text(page) == page.get_text("text", sort=False)
