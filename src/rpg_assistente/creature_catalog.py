"""Catálogo verificável das fichas paranormais de Ordem Paranormal 1.1.

O perfil reconhece a estrutura das fichas, não nomes ou respostas esperadas.
Só lê o SQLite canônico. VD mede desafio de combate; não determina autoridade
narrativa nem garante vitória. A classificação é pelo cabeçalho principal,
nunca pela ocorrência de um elemento no corpo de habilidades/descritivos.
"""

from __future__ import annotations

from collections import Counter
import re
import sqlite3
import unicodedata


_ELEMENTS = ("Sangue", "Morte", "Conhecimento", "Energia", "Medo")
_VD = re.compile(r"(?<!\w)VD\s*(\d{1,5})(?!\d)")
_STAT_LABELS = ("PRESENÇA PERTURBADORA", "PONTOS DE VIDA", "DEFESA")
_NON_NAMES = {
    "ENIGMA DE MEDO", "PRESENÇA PERTURBADORA", "PONTOS DE VIDA", "DEFESA",
    "SENTIDOS", "PERCEPÇÃO", "INICIATIVA", "DESLOCAMENTO", "AÇÕES", "REAÇÕES",
    "CRIATURA DO MEDO", "PADRÃO", "LIVRE", "COMPLETA", "MOVIMENTO",
}
_SCOPE = (
    "Fichas paranormais detectadas por VD, Presença Perturbadora, Pontos de Vida "
    "e Defesa no texto do livro ativo. Classificação pelo elemento principal "
    "do cabeçalho; descritores secundários não formam grupos. Ameaças mundanas, "
    "formas sem VD próprio e entidades apenas narrativas ficam fora deste escopo."
)
_LIMITATION = (
    "VD é um critério de desafio de combate, não de autoridade narrativa ou "
    "vitória garantida. Imunidades, Enigmas de Medo e contexto exigem ler a ficha."
)


def _fold(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(c)
    )


def _key(value: str) -> str:
    return " ".join(re.findall(r"\w+", _fold(value)))


def _lines(text: str) -> list[tuple[str, int, int]]:
    result, offset = [], 0
    for line in text.splitlines(keepends=True):
        result.append((line.strip(), offset, offset + len(line.rstrip("\r\n"))))
        offset += len(line)
    return result


def _name_line(line: str) -> bool:
    return (
        bool(line) and len(line) <= 70 and line.isupper()
        and any(c.isalpha() for c in line)
        and not any(c in line for c in ".,:!?+")
        and not _VD.search(line) and line not in _NON_NAMES
    )


def _name_before(lines: list[tuple[str, int, int]], index: int) -> tuple | None:
    pieces = []
    for item in reversed(lines[max(0, index - 4):index]):
        line, start, end = item
        if not pieces and re.fullmatch(r"VD\s*\d+", line):
            continue  # Alguns cabeçalhos extraem nome, VD, elemento.
        if not _name_line(line) or len(pieces) == 3:
            break
        pieces.append(item)
    if not pieces:
        return None
    pieces.reverse()
    return " ".join(item[0] for item in pieces), pieces[0][1], pieces[-1][2]


def _parse_header(text: str, section: str) -> dict | None:
    lines = _lines(text)
    candidates = []
    for index, (line, start, end) in enumerate(lines):
        element = next((e for e in _ELEMENTS if line == e.upper()), None)
        name = _name_before(lines, index) if element else None
        if name:
            candidates.append({
                "name": name[0], "primary_element": element,
                "classification_basis": "primary_header",
                "spans": [(name[1], end)],
            })
    if candidates:
        # "DAMA DE / SANGUE / SANGUE" inclui um elemento no nome.
        # Preferir o título completo confirmado pelos metadados, depois o maior.
        candidates.sort(key=lambda c: (
            _key(c["name"]) == _key(section), len(c["name"]),
        ), reverse=True)
        best = candidates[0]
        distinct = {c["primary_element"] for c in candidates}
        if len(distinct) > 1:
            return None  # Ambiguidade explícita, nunca classificação silenciosa.
        return best

    # Neste PDF o cabeçalho estilizado MEDO é um glifo sem texto extraível.
    # Aceitar somente uma afirmação textual explícita e um nome confirmado.
    label = next((item for item in lines if item[0] == "CRIATURA DO MEDO"), None)
    if label and _key(section):
        for index in range(len(lines)):
            for count in (1, 2, 3):
                run = lines[index:index + count]
                if (len(run) == count and all(_name_line(item[0]) for item in run)
                        and _key(" ".join(item[0] for item in run)) == _key(section)):
                    return {
                        "name": " ".join(item[0] for item in run),
                        "primary_element": "Medo",
                        "classification_basis": "explicit_creature_label",
                        "spans": [(run[0][1], run[-1][2]), (label[1], label[2])],
                    }
    return None


def _excerpts(text: str, spans: list[tuple[int, int]]) -> str:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 12:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    # Separador indica recortes não contíguos da mesma página.
    return "\n[…]\n".join(text[start:end].strip() for start, end in merged)


def _vd_definition_source(conn: sqlite3.Connection) -> tuple[dict, str] | None:
    """Localiza a definição canônica do indicador, sem fixar página ou chunk.

    Prefere o bloco explicativo do campo da ficha. A expansão explícita da
    sigla entre parênteses é alternativa; uma menção solta a VD não basta.
    """
    term = re.compile(r"valor\s+de\s+desafio", re.IGNORECASE)
    heading = re.compile(r"^\s*Nome\s+e\s+VD\s*\n", re.MULTILINE | re.IGNORECASE)
    expansion = re.compile(r"valor\s+de\s+desafio\s*\(\s*VD\s*\)", re.IGNORECASE)
    candidates = []
    rows = conn.execute(
        """SELECT p.id AS page_id,p.pdf_page,p.printed_page,p.chapter,p.section,p.text,
                  b.title,b.edition,
                  (SELECT c.id FROM chunks c WHERE c.page_id=p.id
                   ORDER BY c.position,c.id LIMIT 1) AS id
           FROM pages p JOIN books b ON b.id=p.book_id
           WHERE b.id=(SELECT id FROM books ORDER BY id LIMIT 1)
           ORDER BY p.pdf_page""",
    )
    for row in rows:
        if row["id"] is None:
            continue
        text = row["text"]
        spans = []
        for match in heading.finditer(text):
            definition = term.search(text, match.end(), match.end() + 250)
            if definition:
                ending = re.search(r"[.!?](?=\s|$)", text[definition.end():definition.end() + 100])
                if ending:
                    spans.append((0, match.start(), definition.end() + ending.end(), definition))
        for match in expansion.finditer(text):
            spans.append((1, match.start(), match.end(), term.search(match.group())))
        for priority, start, end, definition in spans:
            excerpt = text[start:end].strip()
            if not excerpt or len(excerpt) > 400:
                continue
            source = {key: row[key] for key in (
                "id", "page_id", "title", "edition", "pdf_page", "printed_page", "chapter", "section",
            )}
            source.update({"text": excerpt, "source_kind": "catalog",
                           "methods": ["definição do indicador"]})
            candidates.append((priority, row["pdf_page"], source,
                               " ".join(definition.group().split())))
    if not candidates:
        return None
    _, _, source, label = min(candidates, key=lambda candidate: candidate[:2])
    return source, label


def build_creature_catalog(conn: sqlite3.Connection) -> dict:
    """Extrai fichas com cobertura auditável, sem rede, escrita ou lista fixa.

    ``complete_for_detected_statblocks`` significa que todos os candidatos do
    perfil foram extraídos. Não comprova que um PDF danificado não omitiu fichas.
    """
    book_row = conn.execute(
        "SELECT id,title,edition,sha256,page_count FROM books ORDER BY id LIMIT 1"
    ).fetchone()
    coverage = {
        "scanned_pages": 0, "candidate_statblocks": 0, "parsed_statblocks": 0,
        "unparsed_pages": [], "counts_by_primary_element": {},
        "max_vd_by_primary_element": {},
        "complete_for_detected_statblocks": False,
    }
    result = {"entries": [], "sources": [], "scope": _SCOPE, "coverage": coverage,
              "warnings": [_LIMITATION], "profile": "ordem_paranormal_1_1"}
    if book_row is None:
        result["warnings"].append("Não há livro ativo para construir o catálogo.")
        return result
    book = dict(book_row)
    result["book_sha256"] = book["sha256"]
    if ("ordem paranormal" not in _key(book["title"])
            or _key(book["edition"]) not in {"1 1", "v1 1"}):
        result["warnings"].append("O livro não corresponde ao perfil Ordem Paranormal 1.1.")
        return result
    rows = conn.execute(
        """SELECT p.*, (SELECT c.id FROM chunks c WHERE c.page_id=p.id
                        ORDER BY c.position,c.id LIMIT 1) AS chunk_id
           FROM pages p WHERE p.book_id=? ORDER BY p.pdf_page""", (book["id"],),
    )
    for row in rows:
        page = dict(row)
        coverage["scanned_pages"] += 1
        text = page["text"]
        vd_matches = list(_VD.finditer(text))
        if not all(label in text for label in _STAT_LABELS):
            continue
        coverage["candidate_statblocks"] += max(1, len(vd_matches))
        header = _parse_header(text, page["section"] or "")
        if len(vd_matches) != 1 or header is None or page["chunk_id"] is None:
            coverage["unparsed_pages"].append(page["pdf_page"])
            continue
        vd_match = vd_matches[0]
        entry = {
            key: header[key] for key in
            ("name", "primary_element", "classification_basis")
        }
        entry.update({
            "vd": int(vd_match.group(1)), "pdf_page": page["pdf_page"],
            "printed_page": page["printed_page"], "source_id": page["chunk_id"],
        })
        source = {
            "id": page["chunk_id"], "page_id": page["id"],
            "title": book["title"], "edition": book["edition"],
            "pdf_page": page["pdf_page"], "printed_page": page["printed_page"],
            "chapter": page["chapter"], "section": page["section"],
            "text": _excerpts(text, [*header["spans"], vd_match.span()]),
            "methods": ["catálogo estruturado"], "source_kind": "catalog",
        }
        result["entries"].append(entry)
        result["sources"].append(source)
    coverage["parsed_statblocks"] = len(result["entries"])
    coverage["counts_by_primary_element"] = dict(Counter(
        entry["primary_element"] for entry in result["entries"]
    ))
    for entry in result["entries"]:
        key = entry["primary_element"]
        coverage["max_vd_by_primary_element"][key] = max(
            coverage["max_vd_by_primary_element"].get(key, -1), entry["vd"],
        )
    coverage["complete_for_detected_statblocks"] = bool(
        coverage["candidate_statblocks"]
        and coverage["candidate_statblocks"] == coverage["parsed_statblocks"]
        and coverage["scanned_pages"] == book["page_count"]
    )
    if not coverage["complete_for_detected_statblocks"]:
        result["warnings"].append(
            "Cobertura incompleta: não afirmar que o maior VD encontrado é o maior de todo o livro."
        )
    if any(entry["classification_basis"] == "explicit_creature_label"
           for entry in result["entries"]):
        result["warnings"].append(
            "O cabeçalho estilizado de Medo não foi extraído como texto; nesses casos "
            "a classificação usa a afirmação canônica explícita 'CRIATURA DO MEDO'."
        )
    return result


def select_creatures(
    conn: sqlite3.Connection,
    element: str | None = None,
    ranking: str = "highest_per_element",
    limit: int = 20,
) -> dict:
    """Seleciona pelo VD extraído, preservando todos os empates no maior valor.

    Quando um limite impedir devolver todos os vencedores, não devolve um
    subconjunto enganoso. ``all`` é uma listagem paginada e sinaliza truncamento.
    """
    if ranking not in {"highest_per_element", "highest", "all"}:
        raise ValueError("ranking deve ser highest_per_element, highest ou all.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit deve ser um inteiro entre 1 e 100.")
    if element is not None and not isinstance(element, str):
        raise TypeError("element deve ser texto ou None.")
    normalized_element = (
        next((e for e in _ELEMENTS if _key(e) == _key(element)), None)
        if element is not None else None
    )
    if element is not None and normalized_element is None:
        raise ValueError("Elemento não reconhecido pelo perfil do livro.")
    catalog = build_creature_catalog(conn)
    entries = [entry for entry in catalog["entries"]
               if normalized_element is None or entry["primary_element"] == normalized_element]
    if ranking == "highest" and entries:
        maximum = max(entry["vd"] for entry in entries)
        entries = [entry for entry in entries if entry["vd"] == maximum]
    elif ranking == "highest_per_element":
        maxima = {}
        for entry in entries:
            key = entry["primary_element"]
            maxima[key] = max(maxima.get(key, -1), entry["vd"])
        entries = [entry for entry in entries if entry["vd"] == maxima[entry["primary_element"]]]
    entries.sort(key=lambda entry: (-entry["vd"], entry["primary_element"], entry["name"]))
    total_matching = len(entries)
    truncated = total_matching > limit
    warnings = catalog["warnings"].copy()
    if truncated:
        if ranking == "all":
            entries = entries[:limit]
            warnings.append("A listagem foi limitada; há outras fichas no catálogo.")
        else:
            entries = []
            warnings.append("O limite não comporta todos os empates; aumente limit para obter o ranking.")
    by_id = {source["id"]: source for source in catalog["sources"]}
    sources = [by_id[entry["source_id"]] for entry in entries]
    criterion_definition = None
    if entries:
        definition = _vd_definition_source(conn)
        if definition:
            definition_source, label = definition
            sources.append(definition_source)
            criterion_definition = {
                "term": "VD", "label": label, "source_id": definition_source["id"],
                "citation_id": len(sources), "pdf_page": definition_source["pdf_page"],
                "printed_page": definition_source["printed_page"],
            }
        else:
            warnings.append("A definição textual de VD não foi localizada; mantenha a sigla sem expandi-la.")
    return {
        "sources": sources,
        "warnings": warnings,
        "structured_catalog": {
            "profile": catalog["profile"], "book_sha256": catalog.get("book_sha256"),
            "scope": catalog["scope"], "coverage": catalog["coverage"],
            "criterion": "VD impresso; elemento principal do cabeçalho, não descritores secundários.",
            "criterion_definition": criterion_definition,
            "ranking": ranking, "element": normalized_element,
            "entries": entries, "total_matching": total_matching,
            "truncated": truncated,
        },
    }


def render_creature_selection(selection: dict) -> str:
    """Apresenta a agregação sem pedir que uma LLM reescreva números ou fontes."""
    catalog = selection["structured_catalog"]
    citations = {source["id"]: i for i, source in enumerate(selection["sources"], 1)}
    listing = catalog["ranking"] == "all"
    lines = [
        "**Fichas por VD**" if listing else "**Comparação pelo maior VD**",
        "",
        "Esta listagem usa o VD impresso nas fichas." if listing else
        "Para tornar “mais forte” verificável, usei o maior VD impresso nas fichas.",
    ]
    definition = catalog.get("criterion_definition")
    if definition:
        lines[-1] += f" VD significa **{definition['label']}** [{citations[definition['source_id']]}]."
    lines += [
        "",
        "| Elemento principal | Criatura | VD | Fonte |",
        "|---|---|---:|---|",
    ]
    for entry in catalog["entries"]:
        name = entry["name"].replace("|", r"\|").replace("\n", " ")
        lines.append(
            f"| {entry['primary_element']} | {name} | {entry['vd']} | "
            f"[{citations[entry['source_id']]}] |"
        )
    coverage = catalog["coverage"]
    lines += [
        "",
        f"**Cobertura:** {coverage['scanned_pages']} páginas do PDF examinadas; "
        f"{coverage['parsed_statblocks']} fichas paranormais extraídas.",
        "A classificação usa o **elemento principal**, não os descritores secundários.",
    ]
    if not coverage["complete_for_detected_statblocks"]:
        lines.append(
            "A extração está incompleta. Os resultados valem apenas para as fichas "
            "extraídas e não comprovam o máximo de todo o livro."
        )
    else:
        lines.append(
            "Todas as fichas detectadas pelo formato suportado foram processadas. "
            "Isso não inclui entidades que aparecem somente na narrativa."
        )
    if catalog["truncated"]:
        lines.append("A listagem foi limitada; existem outros resultados no catálogo.")
    elif not listing:
        lines.append("Todos os empates no maior VD do conjunto consultado foram preservados.")
    lines += [
        "",
        "**Limite da comparação:** VD não determina sozinho quem venceria um confronto. "
        "Para comparar efeitos, condições ou relevância na história, é preciso consultar "
        "as descrições completas.",
    ]
    return "\n".join(lines)


def creature_catalog_intent(query: str) -> dict | None:
    """Roteamento conservador para rankings estatísticos, sem nomes de criaturas."""
    if not isinstance(query, str):
        raise TypeError("query deve ser texto.")
    words = set(_key(query).split())
    # O contrato só representa máximo global/por elemento e listagem simples.
    # Não descartar silenciosamente um filtro, uma quantidade ou um subconjunto.
    if words & {
        "exceto", "excluindo", "excluir", "exclua", "tirando", "fora", "menos",
        "sem", "nao", "entre", "delas", "deles", "dessas", "desses", "destas", "destes",
        "essas", "esses", "estas", "estes", "aquelas", "aqueles", "ambos", "ambas",
        "anteriores", "citadas", "citados", "ou", "top",
        "dois", "duas", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove",
        "dez", "onze", "doze", "treze", "catorze", "quatorze", "quinze", "dezesseis",
        "dezessete", "dezoito", "dezenove", "vinte", "trinta", "quarenta", "cinquenta",
        "sessenta", "setenta", "oitenta", "noventa", "cem", "cento", "mil",
        "primeira", "primeiras", "primeiro", "primeiros", "segunda", "segundas",
        "segundo", "segundos", "terceira", "terceiras", "terceiro", "terceiros",
    } or any(char.isdigit() for char in query):
        return None
    if words & {"crie", "criar", "gere", "gerar", "missao", "missoes", "historia", "ficha"}:
        return None
    if words & {
        "pv", "vida", "dano", "defesa", "velocidade", "atributos", "resistencia",
        "narrativo", "narrativa", "narrativamente", "lore", "autoridade",
    }:
        return None  # Não substituir outro critério solicitado pelo usuário por VD.
    elements = [element for element in _ELEMENTS if _key(element) in words]
    creature = bool(words & {
        "criatura", "criaturas", "entidade", "entidades", "monstro", "monstros",
        "ameaca", "ameacas",
    })
    comparative = bool(words & {
        "forte", "fortes", "poderosa", "poderosas", "poderoso", "poderosos",
        "maior", "maiores", "alto", "altos", "maximo", "maximos",
    })
    listing = bool(words & {"liste", "listar", "todas", "todos", "catalogo"})
    if (not creature and not ("vd" in words and elements)
            or not (comparative or listing and "vd" in words)):
        return None
    # Múltiplos elementos explícitos precisam de filtro composto, fora deste contrato.
    if len(elements) > 1:
        return None
    per_element = bool(words & {"elemento", "elementos"}) and bool(
        words & {"cada", "por", "todos", "todas"}
    )
    return {
        "element": elements[0] if elements else None,
        "ranking": "all" if listing and not comparative else
                   "highest_per_element" if per_element else "highest",
        "limit": 20,
    }
