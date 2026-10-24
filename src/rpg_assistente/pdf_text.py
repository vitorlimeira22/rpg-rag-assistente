"""Extract text without losing the meaning of verified PDF dice glyphs.

Ordem Paranormal 1.1 embeds the font ``d20-Regular``: its character ``O``
draws a twenty-sided die. The PDF's own legend (PDF page 19) explains that
two consecutive icons mean two dice. Other dice in this book already use
ordinary text (d4, d6, d8, d10 and d12), which must remain unchanged.

This is intentionally a font-specific mapping, not an O -> d20 correction
on arbitrary text. Unknown symbol fonts need their own verified mapping.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import pymupdf


def _is_d20_font(name: str) -> bool:
    # Embedded PDF fonts may have a six-letter subset prefix.
    return re.sub(r"^[A-Z]{6}\+", "", name).casefold() == "d20-regular"


def normalize_dice_spans(spans: Sequence[Mapping]) -> str:
    """Join one line's spans, translating only known glyphs in their font.

    ``3<icon>`` becomes ``3d20``. Signed icons receive an explicit quantity
    annotation: the book's legend means adding/removing dice, not their sum.
    ``-2<icon>`` in creature stat blocks is a different notation and receives
    no quantity annotation. Repeated icons can cross span boundaries.
    Letters O in surrounding prose or other fonts are preserved.
    """
    text = "".join(span["text"] for span in spans)
    glyphs: list[bool] = []
    for span in spans:
        if _is_d20_font(span.get("font", "")):
            glyphs.extend(char == "O" for char in span["text"])
        else:
            glyphs.extend([False] * len(span["text"]))
    if not any(glyphs):
        return text
    result: list[str] = []
    index = 0
    while index < len(text):
        if not glyphs[index]:
            result.append(text[index])
            index += 1
            continue
        end = index + 1
        while end < len(text) and glyphs[end]:
            end += 1
        count = end - index
        # A numeric prefix already expresses the number of dice, e.g. 3O.
        has_count = index > 0 and text[index - 1].isascii() and text[index - 1].isdigit()
        notation = "d20" if count == 1 and has_count else f"{count}d20"
        # PDF p19: +<icon> / -<icon><icon> adjust the dice pool. Keep this
        # semantic distinction when replacing the pictograms with plain text.
        # Never apply it to -2<icon> (numeric prefix, used in creature tests).
        prefix = text[:index].rstrip()
        if prefix and prefix[-1] in "+-−–" and not has_count:
            notation += " [na quantidade de dados]"
        result.append(notation)
        index = end
    return "".join(result)


def extract_page_text(page: pymupdf.Page) -> str:
    """Keep PyMuPDF's original order and whitespace; replace known dice only.

    One text page supplies both the raw string and font metadata. Images are
    excluded, avoiding decoding the book's illustrations during ingestion.
    Locate each original line in sequence rather than reconstructing layout:
    this also preserves unusual PDF line endings and embedded NUL characters.
    """
    text_page = page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT)
    raw = text_page.extractText(sort=False)
    blocks = text_page.extractDICT(sort=False)["blocks"]
    cursor = 0
    copied_until = 0
    result: list[str] = []
    for block in blocks:
        for line in block.get("lines", []):
            spans = line["spans"]
            original = "".join(span["text"] for span in spans)
            position = raw.find(original, cursor)
            if position < 0:
                # A mismatch makes later duplicate lines ambiguous. Do not
                # silently replace a glyph in the wrong part of the page.
                raise ValueError("Não foi possível alinhar o texto e as fontes da página do PDF.")
            cursor = position + len(original)
            normalized = normalize_dice_spans(spans)
            if normalized != original:
                result.append(raw[copied_until:position])
                result.append(normalized)
                copied_until = cursor
    result.append(raw[copied_until:])
    return "".join(result)
