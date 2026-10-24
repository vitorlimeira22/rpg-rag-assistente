"""Apresentação da ficha: regras canônicas e resumo dos dados efetivos.

As escolhas vêm da proposta; a descrição de uma opção reconhecida vem do
trecho do livro. O resumo não repete uma explicação contradita pela validação.
"""
from __future__ import annotations

import re
from .sheet_models import norm, power_name, TRACK_FIRST_POWER, ITEMS, item_key


def _clean(text):
    return " ".join(text.replace("-\n", "").split())


def canonical_descriptions(data, evidence, changed):
    """Substitui somente opções reconhecidas em unidades canônicas completas."""
    catalog, refs = {}, {}
    sources = evidence["sources"]
    by_id = {source["citation_id"]: source for source in sources}

    def register(name, text, source):
        name = power_name(name)
        value = _clean(text)
        # Mantém a unidade completa; uma referência não autoriza cortar efeitos.
        if name and 0 < len(value) <= 2600:
            catalog[name] = value + f" (PDF p.{source['pdf_page']})"
            refs[name] = source["citation_id"]

    options = evidence.get("candidate_options", {})
    for aspect in ("poderes", "rituais", "trilha", "origem"):
        for option in options.get(aspect, []):
            source = by_id.get(option["citation_id"])
            if not source:
                continue
            text, name = source["text"], option["name"]
            if aspect == "trilha":
                name = TRACK_FIRST_POWER.get(norm(name), "")
            elif aspect == "origem":
                match = re.search(r"Perícias [Tt]reinadas\.[^\n]*\n([^\n.]+)\.", text)
                name = match[1] if match else ""
            register(name, text, source)
    # Habilidades iniciais aparecem na apresentação da classe, em vez das
    # listas de poderes. O delimitador seguinte mantém a descrição inteira.
    boundaries = r"\n(?:Habilidade de Trilha|Poder de [A-ZÀ-Ý]|Eclético\.|Perito\.|Escolhido pelo|TRILHAS|GRAU|HABILIDADES|[A-ZÀ-Ý ]{8,})"
    for source in sources:
        text = source["text"]
        for name in ("Ataque Especial", "Eclético", "Perito", "Escolhido pelo Outro Lado"):
            match = re.search(re.escape(name) + r"\.", text, re.I)
            if not match:
                continue
            end = re.search(boundaries, text[match.end():])
            if end:
                register(name, text[match.start():match.end() + end.start()], source)
            elif text.rstrip().endswith("."):
                register(name, text[match.start():], source)
        # Demais poderes podem estar no quadro inteiro de classe recuperado.
        starts = list(re.finditer(r"(?m)^[ \t]*(?:b|•)[ \t]+", text))
        for index, start in enumerate(starts[:-1]):
            block = text[start.end():starts[index + 1].start()].strip()
            if "." in block:
                register(block.split(".", 1)[0], block, source)
    for field in ("powers", "rituals"):
        if field not in changed:
            continue
        for entry in data[field]:
            key = power_name(entry["name"])
            if key in catalog:
                entry["description"] = catalog[key]
    if "equipment" in changed:
        for entry in data["equipment"]:
            key = item_key(entry["name"])
            known = ITEMS.get(key)
            if not known:
                continue
            for source in sources:
                lines = [line.strip() for line in source["text"].splitlines() if line.strip()]
                item_name = key.replace("protecao ", "") if known[2] == "protecao" else key
                index = next((i for i, line in enumerate(lines) if norm(line) == item_name), None)
                if index is None:
                    continue
                values = lines[index + 1:index + (4 if known[2] == "protecao" else 8)]
                if known[2] == "protecao" and len(values) == 3 and values[1] in {"0","I","II","III","IV"}:
                    text = f"Tabela sem modificações: Defesa {values[0]}; categoria {values[1]}; espaços {values[2]}."
                elif known[2] == "geral" and len(values) >= 2 and values[0] in {"0","I","II","III","IV"}:
                    text = f"Tabela sem modificações: categoria {values[0]}; espaços {values[1]}."
                elif len(values) >= 6 and values[0] in {"0","I","II","III","IV"}:
                    text = f"Tabela sem modificações: categoria {values[0]}; dano {values[1]}; crítico {values[2]}; alcance {values[3]}; tipo {values[4]}; espaços {values[5]}."
                else:
                    continue
                modifiers = [name for name in ("favorita", "ferramentas favoritas", "mochila de utilidades")
                             if name in norm(entry["description"])]
                if modifiers:
                    text += " Redução declarada: " + ", ".join(modifiers) + "."
                entry["description"] = text + f" (PDF p.{source['pdf_page']})"
                refs["equipment:" + key] = source["citation_id"]
                break
    return refs


def render_update(data, changed, evidence, report, refs):
    """Resumo determinístico das escolhas aplicadas, separadas das regras."""
    source_ids = {str(source["citation_id"]) for source in evidence["sources"]}
    def citation(number):
        return f" [{number}]" if str(number) in source_ids else ""
    def section_ref(label):
        source = next((s for s in evidence["sources"] if norm(s.get("section") or "") == label), None)
        return citation(source["citation_id"]) if source else ""
    lines = ["Atualizei a ficha como **rascunho para revisão**. As escolhas estão nos campos ao lado."]
    if {"character_class", "nex", "origin", "track", "rank"} & changed:
        lines.append(f"**Conceito:** {data['character_class'].capitalize()}, NEX {data['nex']}%, origem {data['origin'] or 'pendente'}, trilha {data['track'] or 'pendente'}, patente {data['rank'] or 'pendente'}.")
    if "attributes" in changed:
        labels = {"agilidade":"AGI", "forca":"FOR", "intelecto":"INT", "presenca":"PRE", "vigor":"VIG"}
        values = []
        for key, value in data["attributes"].items():
            delta = value - 1
            values.append(f"{labels[key]} {value} ({delta:+d} sobre o inicial)")
        lines.append("**Atributos:** " + "; ".join(values) + "." + section_ref("atributos"))
    if "skills" in changed:
        lines.append("**Perícias:** " + ", ".join(f"{s['name']} +{s['bonus']}" for s in data["skills"]) + "." + section_ref(data["character_class"]))
    if "powers" in changed:
        lines.append("**Habilidades e poderes:** " + "; ".join(p["name"] + citation(refs.get(power_name(p["name"]))) for p in data["powers"]) + ". As descrições conferidas estão na ficha.")
    if "equipment" in changed:
        lines.append("**Equipamento:** " + "; ".join(f"{item['quantity']}× {item['name']} (categoria {item['category']}, {item['spaces']:g} espaço(s) por unidade)" + citation(refs.get("equipment:" + norm(item["name"]))) for item in data["equipment"]) + ".")
    if "rituals" in changed:
        lines.append("**Rituais:** " + ("; ".join(p["name"] + citation(refs.get(power_name(p["name"]))) for p in data["rituals"]) or "nenhum registrado") + ".")
    if changed <= {"name", "player", "notes"}:
        labels = {"name":"nome", "player":"jogador", "notes":"anotações"}
        lines.append("Campos alterados: " + ", ".join(labels[key] for key in sorted(changed)) + ".")
    if report["errors"]:
        lines.append("A conferência ainda aponta pendências nos campos destacados.")
    else:
        lines.append("As verificações implementadas passaram. Efeitos e exceções fora desse escopo ainda precisam ser conferidos no livro.")
    return "\n\n".join(lines)
