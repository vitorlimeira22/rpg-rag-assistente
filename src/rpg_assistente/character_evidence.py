"""Pacote local de evidências para tarefas de personagem.

Seleciona por aspectos e metadados, antes de chamar a LLM. Não escreve regras,
não valida fichas e não depende do reranker. Nomes de opções do pedido ajudam a
localizá-las; a presença de uma seção nunca é certificação de completude.
"""
from __future__ import annotations

from collections import Counter
import re
from time import perf_counter
import unicodedata

from .book_tools import SourceRegistry
from .search import STOPWORDS


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))


def _words(text):
    return set(re.findall(r"\w+", _fold(text))) - STOPWORDS


# São temas do processo de criação, não respostas para uma pergunta de teste.
_ASPECTS = (
    ("atributos", "atributos", "geração atributos pontos distribuir máximo", 1500, 2),
    ("classe", "{class}", "pontos vida iniciais perícias treinadas proficiências", 4200, 1),
    ("progressao", "{class}", "habilidades classe ataque especial eclético escolhido outro lado", 2200, 1),
    ("trilha", "{class}", "trilhas NEX 10", 1000, 1),
    ("poderes", "{class}", "pré-requisitos", 1000, 2),
    ("origem", "origens", "perícias treinadas poder", 950, 1),
    ("pericias", "usando pericias", "bônus treinado", 1100, 2),
    ("lista_pericias", "pericias", "perícias atributo treinada carga", 1800, 1),
    ("armas", "armas", "tabela armas categoria dano crítico espaços", 1500, 1),
    ("protecao", "protecoes", "tabela proteção leve categoria defesa espaços", 650, 2),
    ("equipamento_geral", "equipamento geral", "kit perícia acessórios ferramentas categoria espaços", 1400, 2),
    ("patente", "patente", "tabela patente limite itens categoria", 750, 1),
    ("carga", "capacidade de carga", "capacidade carga força espaços", 700, 1),
)


def _blocks(aspect, pages, all_pages, nex):
    """Unidades canônicas íntegras: cabeçalhos, tabelas e entradas de opção.

    Recortes são substrings do texto salvo. Nunca gera texto de regras nem
    recorta no meio de um pré-requisito para satisfazer o orçamento.
    """
    if aspect == "origem":
        names = []
        for row in pages:
            if re.search(r"TABELA\s+[\d.]+:\s*ORIGENS", row["page_text"], re.I):
                names += re.findall(r"(?m)^\d+(?:-\d+)?\n([^\n]+)\n", row["page_text"])
        names = [name for name in names if any(c.isalpha() for c in name)]
        pattern = (r"(?m)^(?:" + "|".join(map(re.escape, names)) + r")\s*$") if names else None
    else:
        pattern = None
    for row in pages:
        text = row["page_text"]
        spans = []
        if aspect == "classe":
            if re.search(r"PONTOS DE VIDA INICIAIS", text):
                spans = [(0, len(text))]
        elif aspect == "progressao":
            if "HABILIDADES" in text and "PONTOS DE VIDA INICIAIS" not in text:
                spans = [(0, len(text))]
        elif aspect == "atributos":
            for expr in (r"GERAÇÃO\s+DE ATRIBUTOS\b.*?(?=\nEXEMPLO\b|\Z)",
                         r"\nATRIBUTOS\nPersonagens\b.*?(?=\nO primeiro passo\b|\Z)"):
                spans += [m.span() for m in re.finditer(expr, text, re.S)]
        elif aspect == "trilha":
            for m in re.finditer(r"NEX\s*10\s*%\s*[-–]", text):
                # Inclui o título da trilha antes da habilidade, não somente o
                # texto do benefício. Próxima etapa encerra a unidade atual.
                starts = list(re.finditer(r"(?m)^[A-ZÀ-Ý][a-zà-ÿ]+(?:[ \t]+(?:[A-ZÀ-Ý][a-zà-ÿ]+|de|do|da|dos|das)){0,5}[ \t]*$", text[:m.start()]))
                start = starts[-1].start() if starts else m.start()
                end = re.search(r"\nNEX\s*\d+\s*%", text[m.end():])
                stop = m.end() + end.start() if end else len(text)
                spans.append((start, stop))
        elif aspect == "poderes":
            matches = list(re.finditer(r"(?m)^[ \t]*(?:b|•)[ \t]+", text))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                candidate = text[match.start():end]
                boundary = re.search(r"\n(?:Aumento de Atributo|Grau de Treinamento|TRILHAS|NEX\s*\d+%)", candidate)
                if boundary:
                    end = match.start() + boundary.start()
                if 40 < end - match.start() < 1400:
                    spans.append((match.start(), end))
        elif aspect == "origem" and pattern:
            matches = list(re.finditer(pattern, text))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                candidate = text[match.start():end]
                # A tabela é catálogo de nomes; usar somente descrições completas.
                if re.search(r"Perícias [Tt]reinadas\.", candidate):
                    spans.append((match.start(), end))
        elif aspect == "pericias":
            for expr in (r"USANDO PERÍCIAS\b.*?(?=\ncapítulo|\Z)",
                         r"Grau de\s+Treinamento\s+Bônus\s+de Perícia.*?(?=\nTESTE DE PERÍCIA|\Z)"):
                spans += [m.span() for m in re.finditer(expr, text, re.S)]
        elif aspect == "rituais":
            # O cabeçalho elementar + círculo identifica o começo da descrição.
            # A última entrada de cada página pode continuar: não usá-la sem a
            # próxima página, nem oferecer a versão parcial como candidata.
            matches = list(re.finditer(
                r"(?m)^([^\n]+)\n[ \t]*((?:(?:CONHECIMENTO|ENERGIA|MORTE|SANGUE|MEDO)\s+[1-4]\s*)+)\nExecução:", text))
            for index, match in enumerate(matches[:-1]):
                levels = re.findall(r"\b([1-4])\b", match.group(2))
                # Pacote inicial oferece opções do primeiro círculo. Expansões
                # posteriores precisam pesquisar os círculos desbloqueados.
                if levels and set(levels) == {"1"} and "MEDO" not in match.group(2):
                    spans.append((match.start(), matches[index + 1].start()))
        elif aspect == "custo_rituais":
            for expr in (r"TABELA\s+[\d.]+:\s*CUSTO DE\s*RITUAIS.*?(?=\nAPRENDENDO|\Z)",
                         r"O Custo do Paranormal\b.*?(?=\nInvocando o Medo|\Z)"):
                spans += [m.span() for m in re.finditer(expr, text, re.S)]
        elif aspect == "equipamento_geral":
            for expr in (r"TABELA\s+[\d.]+:\s*EQUIPAMENTOS GERAIS.*?(?=\nTABELA\s|\Z)",
                         r"Kit de Perícia\..*?(?=\nUtensílio\.|\Z)"):
                spans += [m.span() for m in re.finditer(expr,text,re.S)]
        elif aspect in {"armas", "protecao", "patente", "lista_pericias"}:
            keyword = {"armas": "ARMAS", "protecao": "PROTEÇÕES", "patente": "PATENTES", "lista_pericias": "PERÍCIAS"}[aspect]
            matches = list(re.finditer(r"TABELA\s+[\d.]+:\s*" + keyword, text))
            for match in matches:
                tail = text[match.end():]
                end = re.search(r"\n(?:TABELA\s|ARMAS IMPROVISADAS|DESCRIÇÃO DAS PERÍCIAS)", tail)
                stop = match.end() + end.start() if end else len(text)
                spans.append((match.start(), stop))
        elif aspect == "carga":
            spans = [m.span() for m in re.finditer(r"Você pode carregar\b.*?(?=\nA regra considera|\nLIMITES DE USO|\Z)", text, re.S)]
        for start, end in spans:
            value = text[start:end].strip()
            if value:
                yield {**row, "text": value, "offset": start}


def character_evidence(conn, settings, *, character_class=None, nex=None,
                       question="", max_chars=None):
    """Retorna fontes canônicas e diagnóstico de aspectos recuperados.

    ``found`` significa somente que houve evidência recuperada para o aspecto;
    ``complete`` permanece False até que um validador confira escolhas concretas.
    ``question`` deve incluir escolhas atuais (origem, trilha, poder, itens).
    """
    started = perf_counter()
    maximum = max_chars if max_chars is not None else min(settings.consult_context_chars, 15000)
    if type(maximum) is not int or maximum < 1000:
        raise ValueError("max_chars deve ser inteiro a partir de 1000.")
    if character_class is not None and character_class not in {"combatente", "especialista", "ocultista"}:
        raise ValueError("Classe não suportada pelo perfil de evidências.")
    rows = [dict(row) for row in conn.execute(
        "SELECT c.id,c.text,c.position,p.id AS page_id,p.pdf_page,p.printed_page,p.chapter,p.section,"
        "p.text AS page_text,b.title,b.edition FROM chunks c JOIN pages p ON p.id=c.page_id "
        "JOIN books b ON b.id=p.book_id WHERE b.id=(SELECT id FROM books ORDER BY id LIMIT 1) "
        "ORDER BY p.pdf_page,c.position"
    )]
    for row in rows:
        prefix = r"\s+".join(map(re.escape, row["text"].split()[:10]))
        match = re.search(prefix, row["page_text"])
        row["chunk_offset"] = match.start() if match else row["position"] * 800
    registry = SourceRegistry(max_sources=24, max_total_chars=maximum)
    frequency = Counter(word for row in rows for word in _words(row["text"]))
    focus = _words(question) - {"personagem", "ficha", "build", "completo", "completa", "sugira",
                                "criar", "monte", "montar", "nex", "combatente", "especialista", "ocultista"}
    found, missing, requested = [], [], []
    candidate_options = {key: [] for key in ("origem", "trilha", "poderes", "rituais")}
    seen_keys = set()
    pages = list({row["page_id"]: row for row in reversed(rows)}.values())
    pages.sort(key=lambda row: row["pdf_page"])
    ritual_count = min(6, max(3, 2 + (nex or 5) // 5))
    aspects = list(_ASPECTS)
    if character_class == "ocultista":
        aspects += [("rituais", "descricao dos rituais", "", 4300, ritual_count),
                    ("custo_rituais", "rituais", "custo", 1000, 2)]
    ritual_sources = 0
    for aspect, section, query, budget, count in aspects:
        if aspect == "poderes" and nex is not None and nex < 15:
            # Habilidades iniciais, origem e trilha têm slots próprios. A
            # primeira escolha ordinária de poder de classe ocorre em 15%.
            continue
        if section == "{class}" and not character_class:
            missing.append(aspect)
            requested.append(aspect)
            continue
        section = section.replace("{class}", character_class or "")
        requested.append(aspect)
        candidates = []
        query_terms = _words(query)
        selected_pages = []
        for row in pages:
            title = _fold(row["section"] or "")
            # Não deixar um pedido de equipamento cair em modificações ou uma
            # perícia citada no exemplo de outra classe ocupar o slot correto.
            eligible = (section in _fold(row["chapter"] or "") if aspect == "lista_pericias" else title == section)
            if not eligible:
                continue
            selected_pages.append(row)
        for row in _blocks(aspect, selected_pages, pages, nex):
            terms = _words(row["text"])
            score = sum(1.0 for word in query_terms & terms)
            if aspect in {"origem", "trilha", "poderes"}:
                # Um nome de opção pedido explicitamente tem precedência sobre
                # palavras genéricas que também aparecem em outras descrições.
                title = re.sub(r"^(?:b|•)\s+", "", row["text"].splitlines()[0]).split(".", 1)[0].strip()
                if title and re.search(r"(?<!\w)" + re.escape(_fold(title)) + r"(?!\w)", _fold(question)):
                    score += 1000
            # Termos raros favorecem nomes escolhidos, sem uma lista de builds.
            score += sum(min(4.0, 20 / max(1, frequency[word])) for word in focus & terms)
            if aspect == "poderes" and nex is not None:
                required = re.search(r"Pré-requisit[^.]*\bNEX\s*(\d+)\s*%", row["text"], re.I)
                if required and int(required.group(1)) > nex:
                    continue
            if aspect in {"armas", "patente", "protecao", "progressao", "pericias"}:
                score += 4 if "tabela" in terms else 0
            if aspect == "atributos":
                score += 5 if "geracao" in terms else 0
            if aspect == "poderes":
                score += 3 if re.search(r"(?m)^\s*(?:b|•)\s+", row["text"]) else 0
            candidates.append((score, row))
        candidates.sort(key=lambda item: (-item[0], item[1]["pdf_page"], item[1]["position"]))
        used, selected = 0, []
        scaled_budget = max(500, round(budget * maximum / 15000))
        for _, row in candidates:
            key = (row["page_id"], row["text"])
            if key in seen_keys:
                selected.append(key)
                if len(selected) >= count:
                    break
                continue
            text = row["text"]
            kind = "page" if text == row["page_text"].strip() else "chunk"
            if used + len(text) > scaled_budget:
                if selected:
                    continue
                # Uma unidade canônica íntegra vale mais que um preview cortado.
                if len(text) > scaled_budget + 500:
                    continue
            remaining = maximum - registry.total_chars
            if len(text) > remaining:
                continue
            # Preserva uma âncora canônica próxima ao início do recorte.
            page_chunks = [part for part in rows if part["page_id"] == row["page_id"]]
            eligible = [part for part in page_chunks if part["chunk_offset"] <= row["offset"]]
            anchor = eligible[-1] if eligible else page_chunks[0]
            source = registry.add({**row, "id": anchor["id"], "text": text, "source_kind": kind,
                                   "methods": ["cobertura por seções"], "original_chars": len(text)})
            if source and (not source.get("reused") or source["text"] == text):
                selected.append(key)
                seen_keys.add(key)
                used += len(text)
                if aspect == "rituais":
                    ritual_sources += 1
                if aspect in candidate_options:
                    name = text.splitlines()[0].strip()
                    if aspect == "poderes":
                        name = re.sub(r"^(?:b|•)\s+", "", text).split(".", 1)[0]
                        name = " ".join(name.replace("-\n", "").split())
                    candidate_options[aspect].append({"name": name, "citation_id": source["citation_id"]})
            if len(selected) >= count:
                break
        (found if selected else missing).append(aspect)
    warnings = []
    if character_class == "ocultista" and ritual_sources < ritual_count:
        missing.append("rituais_adicionais")
        warnings.append(f"Foram recuperadas {ritual_sources} descrições completas de rituais; o pedido precisa de mais opções para concluir a seleção.")
    if missing:
        warnings.append("Evidências ainda ausentes para: " + ", ".join(missing) + ".")
    return {
        "sources": registry.sources, "warnings": warnings + registry.warnings,
        "candidate_options": candidate_options,
        "coverage": {"requested": requested, "found": found, "missing": missing,
                     "complete": False, "character_class": character_class, "nex": nex,
                     "ritual_candidates": ritual_sources},
        "retrieval": {"strategy": "character_sections", "rerank_status": "not_applicable",
                      "elapsed_ms": round((perf_counter() - started) * 1000),
                      "source_chars": registry.total_chars},
    }
