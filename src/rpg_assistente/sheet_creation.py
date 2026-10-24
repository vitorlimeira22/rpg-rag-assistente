"""Completa escolhas de criação a partir do perfil mecânico conhecido.

Só atua quando o usuário pede uma ficha nova/completa. Edição manual e revisão
não passam por este preenchimento. A IA sugere preferências; fatos de catálogo,
habilidades obrigatórias e a contagem das perícias pertencem ao perfil Python.
"""
from __future__ import annotations

import re
from .sheet_models import (SheetData, Skill, Entry, Equipment, SKILLS, ITEMS,
                           AMMUNITION, TRACK_FIRST_POWER, RANKS, norm, power_name, item_key)

STARTERS = {"combatente":["Ataque Especial"], "especialista":["Eclético","Perito"],
            "ocultista":["Escolhido pelo Outro Lado"]}
SKILL_LABELS = {norm(name):name for name in (
    "Acrobacia","Adestramento","Artes","Atletismo","Atualidades","Ciências","Crime","Diplomacia",
    "Enganação","Fortitude","Furtividade","Iniciativa","Intimidação","Intuição","Investigação",
    "Luta","Medicina","Ocultismo","Percepção","Pilotagem","Pontaria","Profissão","Reflexos",
    "Religião","Sobrevivência","Tática","Tecnologia","Vontade")}
SKILL_ATTRIBUTES = {
    **dict.fromkeys(["acrobacia","crime","furtividade","iniciativa","pilotagem","pontaria","reflexos"],"agilidade"),
    **dict.fromkeys(["atletismo","luta"],"forca"), "fortitude":"vigor",
    **dict.fromkeys(["adestramento","artes","diplomacia","enganacao","intimidacao","intuicao","percepcao","religiao","vontade"],"presenca"),
}
PREFERENCES = {
    "combatente":["Luta","Pontaria","Fortitude","Reflexos","Iniciativa","Atletismo","Percepção","Vontade","Tática"],
    "especialista":["Investigação","Percepção","Tecnologia","Crime","Furtividade","Intuição","Medicina","Vontade","Reflexos","Iniciativa","Atualidades","Ciências"],
    "ocultista":["Ocultismo","Vontade","Investigação","Percepção","Reflexos","Fortitude","Ciências","Religião","Medicina","Diplomacia","Intuição"],
}


def _favorite_weapon(sheet):
    if sheet.character_class != "combatente" or sheet.nex < 10 or norm(sheet.track) != "aniquilador":
        return
    ability = next((p for p in sheet.powers if power_name(p.name) == "a favorita"),None)
    if ability is None:
        return
    match = re.search(r"\(([^)]+)\)",ability.name)
    choices = [i for i in sheet.equipment if ITEMS.get(item_key(i.name),(0,0,""))[2] in {"simples","tatica","pesada"}]
    weapon = next((i for i in choices if norm(i.name) == norm(match[1])),None) if match else next(iter(choices),None)
    if weapon:
        ability.name = f"A Favorita ({weapon.name})"
        base = ITEMS[item_key(weapon.name)][0]
        if weapon.category in (base,max(0,base-1)):
            weapon.category = max(0,base-1)
            if "favorita" not in norm(weapon.description):
                weapon.description += " Redução declarada: A Favorita."


def complete_creation(sheet: SheetData, evidence: dict, question: str = "") -> tuple[SheetData, list[str]]:
    if sheet.nex not in (5,10,15):
        return sheet, []
    notices = []
    powers = {power_name(p.name) for p in sheet.powers}
    required = list(STARTERS[sheet.character_class])
    if sheet.nex >= 10 and norm(sheet.track) in TRACK_FIRST_POWER:
        required.append(TRACK_FIRST_POWER[norm(sheet.track)].title())
    for name in required:
        if power_name(name) not in powers:
            sheet.powers.append(Entry(name=name))
            powers.add(power_name(name))
            notices.append("Habilidades obrigatórias de classe/trilha foram acrescentadas à criação.")

    existing = {norm(s.name).split("(",1)[0].strip():s for s in sheet.skills if s.bonus >= 5
                and norm(s.name).split("(",1)[0].strip() in SKILLS}
    mandatory, origin_power_keys = [], set()
    if sheet.character_class == "combatente":
        mandatory += [next((s for s in ("luta","pontaria") if s in existing),
                           "pontaria" if sheet.attributes.agilidade > sheet.attributes.forca else "luta")]
        mandatory += [next((s for s in ("fortitude","reflexos") if s in existing),
                           "reflexos" if sheet.attributes.agilidade > sheet.attributes.vigor else "fortitude")]
    elif sheet.character_class == "ocultista":
        mandatory += ["ocultismo","vontade"]
    for option in evidence.get("candidate_options",{}).get("origem",[]):
        if norm(option["name"]) != norm(sheet.origin):
            continue
        source = next((s for s in evidence["sources"] if s["citation_id"] == option["citation_id"]),None)
        match = re.search(r"Perícias [Tt]reinadas\.\s*([^.]*)\.", source["text"]) if source else None
        if match:
            text = norm(match[1])
            mandatory += [key for key in sorted(SKILLS) if re.search(r"(?<!\w)"+re.escape(key)+r"(?!\w)",text)]
        ability = re.search(r"Perícias [Tt]reinadas\.[^\n]*\n([^\n.]+)\.", source["text"]) if source else None
        if ability:
            origin_power_keys.add(power_name(ability[1]))
        if ability and power_name(ability[1]) not in powers:
            sheet.powers.append(Entry(name=ability[1].strip()))
            powers.add(power_name(ability[1]))
            notices.append("O poder da origem foi acrescentado conforme a descrição consultada.")
    count = {"combatente":5,"especialista":9,"ocultista":7}[sheet.character_class] + sheet.attributes.intelecto
    if "treinamento em pericia" in powers:
        count += 2
    chosen = list(dict.fromkeys([*mandatory,*existing,*map(norm,PREFERENCES[sheet.character_class]),*sorted(SKILLS)]))[:count]
    skills = []
    for key in chosen:
        original = existing.get(key)
        skills.append(Skill(name=original.name if original else SKILL_LABELS[key], bonus=5,
                            attribute=SKILL_ATTRIBUTES.get(key,"intelecto"),
                            notes=original.notes if original else "Sugestão de criação; confira o conceito."))
    if [s.model_dump() for s in skills] != [s.model_dump() for s in sheet.skills]:
        notices.append("Perícias de criação foram completadas com as obrigatórias e sugestões compatíveis com a classe. Você pode trocar as sugestões no editor.")
    sheet.skills = skills
    if sheet.nex < 15 and norm(sheet.origin) != "cultista arrependido":
        allowed = {power_name(name) for name in required} | origin_power_keys
        kept = [p for p in sheet.powers if power_name(p.name) in allowed]
        if len(kept) != len(sheet.powers):
            notices.append("Antes de NEX 15%, foram mantidas as habilidades iniciais, de origem e de trilha; a escolha ordinária de poder de classe ainda não está disponível.")
        sheet.powers = kept
    if sheet.character_class == "especialista":
        choices = [s.name for s in skills if norm(s.name) not in {"luta","pontaria"}][:2]
        for entry in sheet.powers:
            if norm(entry.name) == "perito" and len(choices) == 2:
                entry.name = "Perito (" + ", ".join(choices) + ")"
                notices.append("Foram sugeridas duas perícias treinadas para Perito; você pode alterar essa escolha.")

    supported_inventory = []
    for item in sheet.equipment:
        if item_key(item.name) not in ITEMS:
            explicit = bool(re.search(r"(?<!\w)"+re.escape(norm(item.name))+r"(?!\w)",norm(question)))
            if not explicit:
                notices.append(f"{item.name}: sugestão fora do catálogo implementado; não foi incluída com valores presumidos. Você pode adicioná-la manualmente após conferir o livro.")
                continue
        supported_inventory.append(item)
    sheet.equipment = supported_inventory
    for item in sheet.equipment:
        known = ITEMS.get(item_key(item.name))
        if known and not any(marker in norm(item.description) for marker in
                             ("favorita","mochila de utilidades","modific","aprimor","ferramentas favoritas")):
            if (item.category,item.spaces) != known[:2]:
                item.category,item.spaces = known[:2]
                notices.append("Categorias e espaços de itens sem modificações foram preenchidos a partir do catálogo conferido.")
    _favorite_weapon(sheet)
    names = {norm(item.name) for item in sheet.equipment}
    for item in list(sheet.equipment):
        ammo = AMMUNITION.get(norm(item.name))
        known = ITEMS.get(norm(ammo)) if ammo else None
        if known and norm(ammo) not in names:
            sheet.equipment.append(Equipment(name=ammo,quantity=1,category=known[0],spaces=known[1]))
            names.add(norm(ammo))
            notices.append("Foi incluído um pacote de munição para cada tipo necessário às armas escolhidas; ajuste a quantidade para a missão.")
    limits = RANKS.get(norm(sheet.rank))
    if limits:
        limits = list(limits)
        if any(norm(s.name).startswith("profissao") and s.bonus == 5 for s in skills):
            limits[0] += 1
        ammunition = {norm(AMMUNITION[norm(i.name)]) for i in sheet.equipment if norm(i.name) in AMMUNITION}
        priority = lambda i: (0 if norm(i.name) in AMMUNITION else 1 if norm(i.name) in ammunition else 2)
        inventory, used = [], [0]*5
        for item in sorted(sheet.equipment,key=priority):
            if item.category == 0:
                inventory.append(item)
                continue
            room = max(0,limits[item.category-1] - used[item.category])
            explicit = bool(re.search(r"(?<!\w)" + re.escape(norm(item.name)) + r"(?!\w)",norm(question)))
            if item.quantity > room and not explicit:
                notices.append(f"{item.name}: quantidade ajustada à patente {sheet.rank}; itens incompatíveis sugeridos pelo modelo não foram incluídos.")
                if room == 0:
                    continue
                item.quantity = room
            inventory.append(item)
            used[item.category] += item.quantity
        sheet.equipment = inventory
    if not sheet.equipment:
        # Um pedido genérico de criação permite sugerir equipamento básico.
        # Não substituir um item explícito incompatível: ele é mantido acima
        # para que o relatório explique o conflito de patente.
        sheet.equipment = [Equipment(name="Faca",category=0,spaces=1),
                           Equipment(name="Corda",category=0,spaces=1)]
        if sheet.character_class == "especialista":
            kit = next((s.name for s in skills if norm(s.name) in {"tecnologia","crime","medicina"}),None)
            if kit:
                sheet.equipment[1] = Equipment(name=f"Kit de perícia ({kit})",category=0,spaces=1)
        notices.append("Foi sugerido um inventário inicial básico compatível com a patente. Revise arma, proteção e itens específicos para a missão.")
        _favorite_weapon(sheet)
    return sheet, list(dict.fromkeys(notices))
