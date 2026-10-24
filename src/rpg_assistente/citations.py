"""Confere os identificadores citados; não valida o sentido das afirmações."""

import re
from unicodedata import decimal


def _decimal_id(digits: str) -> str:
    return "".join(str(decimal(char)) for char in digits).lstrip("0") or "0"


def normalize_citations(text: str) -> str:
    """Converte somente referências numéricas equivalentes ao formato ``[n]``.

    Alguns modelos usam ``【n】`` ou ``【n†】``. Normalizar a apresentação não
    confirma que a fonte existe; ``citation_status`` continua responsável por isso.
    URLs, nomes e outras marcações não são interpretados como identificadores.
    """
    return re.sub(r"【(\d+)†?】", lambda match: f"[{_decimal_id(match.group(1))}]", text)


def citation_status(text: str, source_count: int) -> str:
    """Aceita somente referências às fontes existentes, sem converter inteiros enormes.

    A resposta da LLM é entrada não confiável. Comparar representações decimais
    normalizadas evita a exceção que ``int()`` produz com milhares de dígitos.
    Zeros à esquerda e dígitos decimais Unicode mantêm o significado numérico.
    """
    allowed = {str(number) for number in range(1, source_count + 1)}
    found = False
    for match in re.finditer(r"\[(\d+)\]", text):
        found = True
        number = _decimal_id(match.group(1))
        if number not in allowed:
            return "invalid"
    return "present" if found else "absent"
