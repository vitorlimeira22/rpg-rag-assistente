"""Ficha persistente e validação delimitada de Ordem Paranormal 1.1.

Rascunhos podem ser salvos mesmo incompletos. Nunca se confunde salvar com
certificar uma ficha: o relatório explicita verificações e limites. Valores
derivados são calculados aqui, nunca aceitos da LLM ou do navegador.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .characters import Attributes, CLASS_RULES, CharacterInput, validate_character


def norm(text: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                            if not unicodedata.combining(c)).split())


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=3000)


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="", max_length=80)
    bonus: StrictInt = Field(default=5, ge=-20, le=50,
                            description="Bônus de treinamento (0/5/10/15), sem modificadores situacionais.")
    attribute: Literal["", "agilidade", "forca", "intelecto", "presenca", "vigor"] = ""
    notes: str = Field(default="", max_length=200)


class Equipment(Entry):
    quantity: StrictInt = Field(default=1, ge=1, le=99)
    category: StrictInt = Field(default=0, ge=0, le=4)
    spaces: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)


class SheetData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="Novo agente", min_length=1, max_length=80)
    player: str = Field(default="", max_length=80)
    character_class: Literal["combatente", "especialista", "ocultista"] = "combatente"
    nex: StrictInt = Field(default=5, ge=0, le=99)
    origin: str = Field(default="", max_length=100)
    track: str = Field(default="", max_length=100)
    rank: str = Field(default="Recruta", max_length=80)
    attributes: Attributes = Field(default_factory=lambda: Attributes(
        agilidade=1, forca=1, intelecto=1, presenca=1, vigor=1))
    skills: list[Skill] = Field(default_factory=list, max_length=40)
    powers: list[Entry] = Field(default_factory=list, max_length=30)
    equipment: list[Equipment] = Field(default_factory=list, max_length=30)
    rituals: list[Entry] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=5000)
    # Campo de leitura aceito no roundtrip; é sempre descartado e recalculado.
    derived: dict = Field(default_factory=dict, exclude=True)


GROWTH = {"combatente": (4, 2, 3), "especialista": (3, 3, 4), "ocultista": (2, 4, 5)}
RANKS = {"recruta": (2, 0, 0, 0), "operador": (3, 1, 0, 0),
         "agente especial": (3, 2, 1, 0), "oficial de operacoes": (3, 3, 2, 1),
         "agente de elite": (3, 3, 3, 2)}
SKILLS = {norm(name) for name in (
    "Acrobacia", "Adestramento", "Artes", "Atletismo", "Atualidades", "Ciências", "Crime",
    "Diplomacia", "Enganação", "Fortitude", "Furtividade", "Iniciativa", "Intimidação",
    "Intuição", "Investigação", "Luta", "Medicina", "Ocultismo", "Percepção", "Pilotagem",
    "Pontaria", "Profissão", "Reflexos", "Religião", "Sobrevivência", "Tática", "Tecnologia", "Vontade",
)}
ORIGINS = {norm(name) for name in (
    "Acadêmico", "Agente de Saúde", "Amnésico", "Artista", "Atleta", "Chef", "Criminoso",
    "Cultista Arrependido", "Desgarrado", "Engenheiro", "Executivo", "Investigador", "Lutador",
    "Magnata", "Mercenário", "Militar", "Operário", "Policial", "Religioso", "Servidor Público",
    "Teórico da Conspiração", "T.I.", "Trabalhador Rural", "Trambiqueiro", "Universitário", "Vítima",
)}
TRACKS = {
    "combatente": {norm(name) for name in ("Aniquilador", "Comandante de Campo", "Guerreiro", "Operações Especiais", "Tropa de Choque")},
    "especialista": {norm(name) for name in ("Atirador de Elite", "Infiltrador", "Médico de Campo", "Negociador", "Técnico")},
    "ocultista": {norm(name) for name in ("Conduíte", "Flagelador", "Graduado", "Intuitivo", "Lâmina Paranormal")},
}
TRACK_FIRST_POWER = {
    "aniquilador": "a favorita", "comandante de campo": "inspirar confianca",
    "guerreiro": "tecnica letal", "operacoes especiais": "iniciativa aprimorada",
    "tropa de choque": "casca grossa", "atirador de elite": "mira de elite",
    "infiltrador": "ataque furtivo", "medico de campo": "paramedico", "negociador": "eloquencia",
    "tecnico": "inventario otimizado", "conduite": "ampliar ritual", "flagelador": "poder do flagelo",
    "graduado": "saber ampliado", "intuitivo": "mente sa", "lamina paranormal": "lamina maldita",
}


def power_name(name: str) -> str:
    """Parâmetros da habilidade não mudam sua identidade para validação."""
    return re.split(r"\s*\(|\s*\[|\s*:\s*|\s+[-–—]\s+", norm(name), maxsplit=1)[0].strip()
# Dados mecânicos conferidos nas tabelas, sem reproduzir descrições do livro.
# (categoria, espaços, grupo); modificações e reduções exigem revisão separada.
ITEMS = {
    "kit de pericia": (0,1,"geral"), "utensilio": (1,1,"geral"), "vestimenta": (1,1,"geral"),
    "algemas": (0,1,"geral"), "arpeu": (0,1,"geral"), "bandoleira": (1,1,"geral"),
    "binoculos": (0,1,"geral"), "bloqueador de sinal": (1,1,"geral"), "cicatrizante": (1,1,"geral"),
    "corda": (0,1,"geral"), "equipamento de sobrevivencia": (0,2,"geral"),
    "lanterna tatica": (1,1,"geral"), "mascara de gas": (0,1,"geral"),
    "oculos de visao termica": (1,1,"geral"), "pe de cabra": (0,1,"geral"),
    "pistola de dardos": (1,1,"geral"), "pistola sinalizadora": (0,1,"geral"),
    "soqueira": (0,1,"geral"), "spray de pimenta": (1,1,"geral"), "taser": (1,1,"geral"),
    "traje hazmat": (1,2,"geral"), "foguete": (1,1,"municao"), "combustivel": (1,1,"municao"),
    "faca": (0, 1, "simples"), "martelo": (0, 1, "simples"),
    "punhal": (0, 1, "simples"), "bastao": (0, 1, "simples"),
    "machete": (0, 1, "simples"), "lanca": (0, 1, "simples"),
    "cajado": (0, 2, "simples"), "arco": (0, 2, "simples"),
    "besta": (0, 2, "simples"), "pistola": (1, 1, "simples"),
    "revolver": (1, 1, "simples"), "fuzil de caca": (1, 2, "simples"),
    "machadinha": (0, 1, "tatica"), "nunchaku": (0, 1, "tatica"),
    "corrente": (0, 1, "tatica"), "espada": (1, 1, "tatica"),
    "florete": (1, 1, "tatica"), "machado": (1, 1, "tatica"),
    "maca": (1, 1, "tatica"), "acha": (1, 2, "tatica"),
    "gadanho": (1, 2, "tatica"), "katana": (1, 2, "tatica"),
    "marreta": (1, 2, "tatica"), "montante": (1, 2, "tatica"),
    "motoserra": (1, 2, "tatica"), "arco composto": (1, 2, "tatica"),
    "balestra": (1, 2, "tatica"), "submetralhadora": (1, 1, "tatica"),
    "espingarda": (1, 2, "tatica"), "fuzil de assalto": (2, 2, "tatica"),
    "fuzil de precisao": (3, 2, "tatica"), "bazuca": (3, 2, "pesada"),
    "lanca-chamas": (3, 2, "pesada"), "metralhadora": (2, 2, "pesada"),
    "balas curtas": (0, 1, "municao"), "balas longas": (1, 1, "municao"),
    "flechas": (0, 1, "municao"), "cartuchos": (1, 1, "municao"),
    "protecao leve": (1, 2, "protecao"), "protecao pesada": (2, 5, "protecao"),
    "escudo": (1, 2, "protecao"),
}


def item_key(name: str) -> str:
    key = norm(name)
    if key.startswith("kit de pericia (") or key.startswith("kit de pericia:"):
        return "kit de pericia"
    return key
AMMUNITION = {
    "pistola":"Balas curtas", "revolver":"Balas curtas", "submetralhadora":"Balas curtas",
    "fuzil de caca":"Balas longas", "fuzil de assalto":"Balas longas", "fuzil de precisao":"Balas longas",
    "metralhadora":"Balas longas", "espingarda":"Cartuchos", "arco":"Flechas",
    "arco composto":"Flechas", "besta":"Flechas", "balestra":"Flechas",
    "bazuca":"Foguete", "lanca-chamas":"Combustível",
}


def validate_sheet(sheet: SheetData) -> dict:
    errors, warnings, derived = [], [], {}
    completion_issues = []
    def error(field, message):
        errors.append({"field": field, "message": message})
    supported = sheet.nex in (5, 10, 15)
    attrs = sheet.attributes
    base = validate_character(CharacterInput(name=sheet.name, character_class=sheet.character_class,
                                             nex=5, attributes=attrs))
    if supported:
        errors.extend(base["errors"])
    if not supported:
        warnings.append("Cálculos automáticos disponíveis em NEX 5%, 10% e 15%. Este rascunho pode ser editado, mas a evolução fora desse intervalo precisa de revisão.")
    if supported and base["valid"]:
        levels = sheet.nex // 5
        initial, growth = CLASS_RULES[sheet.character_class], GROWTH[sheet.character_class]
        derived = {
            "pv": initial["pv"] + attrs.vigor + (levels - 1) * (growth[0] + attrs.vigor),
            "pe": initial["pe"] + attrs.presenca + (levels - 1) * (growth[1] + attrs.presenca),
            "san": initial["san"] + (levels - 1) * growth[2],
            "defense": 10 + attrs.agilidade, "pe_per_turn": levels,
            "capacity": 5 * attrs.forca if attrs.forca > 0 else 2,
        }
        # Efeitos simples cujo valor não depende de escolhas omitidas ou ações.
        # Fontes PDF26,29 e39. O resto permanece uma pendência explícita.
        origin = norm(sheet.origin)
        if origin == "desgarrado":
            derived["pv"] += levels
        elif origin == "vitima":
            derived["san"] += levels
        elif origin == "universitario":
            derived["pe"] += (levels + 1) // 2
            derived["pe_per_turn"] += 1
        elif origin == "cultista arrependido":
            derived["san"] -= initial["san"] // 2
        if sheet.character_class == "especialista" and sheet.nex >= 10 and norm(sheet.track) == "tecnico":
            strength = attrs.forca + attrs.intelecto
            derived["capacity"] = 5 * strength if strength > 0 else 2
    if not sheet.origin:
        error("origin", "Escolha uma origem para completar a ficha.")
    elif norm(sheet.origin) not in ORIGINS:
        warnings.append("Origem fora do catálogo do livro: suas perícias e benefícios exigem revisão como regra da mesa.")
    if supported and sheet.nex >= 10 and not sheet.track:
        error("track", "Escolha uma trilha a partir de NEX 10%.")
    elif sheet.track and norm(sheet.track) not in TRACKS[sheet.character_class]:
        error("track", "Escolha uma trilha da classe: " + ", ".join(sorted(TRACKS[sheet.character_class])) + ". Poderes de classe não são trilhas.")
    if not sheet.powers:
        error("powers", "Registre as habilidades de classe, origem e trilha, além dos poderes escolhidos.")
    if not sheet.equipment:
        error("equipment", "O inventário ainda está vazio.")
    if sheet.character_class == "ocultista" and not sheet.rituals:
        error("rituals", "Registre os rituais conhecidos pelo ocultista.")
    # O campo bonus representa o grau de treinamento; não contém penalidade de
    # carga nem bônus de origem, equipamento ou circunstância. Profissão pode
    # trazer especialização entre parênteses, sem perder sua identidade.
    trained = [norm(s.name).split("(", 1)[0].strip() for s in sheet.skills if s.name and s.bonus >= 5]
    powers = {power_name(p.name) for p in sheet.powers}
    for field in ("powers", "rituals"):
        names_in_field = [power_name(entry.name) for entry in getattr(sheet, field)]
        if any(not name for name in names_in_field):
            error(field, "Preencha o nome de cada habilidade/ritual ou remova linhas vazias.")
        if len(names_in_field) != len(set(names_in_field)):
            warnings.append("Há nomes de habilidades/rituais repetidos; confira se são escolhas distintas ou registros duplicados.")
    names = [norm(s.name) for s in sheet.skills if s.name]
    if len(names) != len(set(names)):
        error("skills", "Uma perícia aparece mais de uma vez; registre-a uma única vez.")
    for index, skill in enumerate(sheet.skills):
        if not skill.name:
            error(f"skills.{index}.name", "Preencha o nome da perícia ou remova a linha vazia.")
        elif norm(skill.name).split("(", 1)[0].strip() not in SKILLS:
            error(f"skills.{index}.name", "Perícia não encontrada no catálogo do livro.")
        if skill.bonus not in (0, 5, 10, 15):
            error(f"skills.{index}.bonus", "Informe o bônus de treinamento: 0, 5, 10 ou 15. Anote modificadores separadamente.")
        elif supported and skill.bonus > 5:
            error(f"skills.{index}.bonus", "No recorte NEX 5/10/15, o treinamento básico é +5; bônus de outras fontes devem ir nas anotações.")
    if supported:
        required = {"combatente": {"ataque especial"}, "especialista": {"ecletico", "perito"},
                    "ocultista": {"escolhido pelo outro lado"}}[sheet.character_class]
        for ability in sorted(required - powers):
            error("powers", f"Registre a habilidade inicial {ability.title()} e suas escolhas, quando houver.")
        if sheet.nex >= 10 and norm(sheet.track) in TRACKS[sheet.character_class]:
            ability = TRACK_FIRST_POWER[norm(sheet.track)]
            if ability not in powers:
                error("powers", f"A trilha {sheet.track} concede {ability.title()} em NEX 10%; registre essa habilidade.")
        expected = {"combatente": 5, "especialista": 9, "ocultista": 7}[sheet.character_class] + attrs.intelecto
        if "treinamento em pericia" in powers:
            expected += 2
        if len(trained) != expected:
            error("skills", f"Conferência básica: esperadas {expected} perícias treinadas (classe + Intelecto + 2 de origem; Treinamento em Perícia, quando registrado, adiciona 2). Há {len(trained)}. Exceções precisam de revisão.")
        if sheet.character_class == "combatente":
            for choices in ({"luta", "pontaria"}, {"fortitude", "reflexos"}):
                if not choices.intersection(trained):
                    error("skills", "Combatente precisa de Luta ou Pontaria e Fortitude ou Reflexos treinadas.")
        if sheet.character_class == "ocultista" and not {"ocultismo", "vontade"} <= set(trained):
            error("skills", "Ocultista precisa de Ocultismo e Vontade treinadas.")
        if "aumento de atributo" in powers:
            error("powers", "Aumento de Atributo da progressão começa em NEX 20%; não ocupa a escolha de poder em NEX 15%.")
        for name, minimum in (("protecao pesada", 30), ("segurar o gatilho", 60)):
            if name in powers and sheet.nex < minimum:
                error("powers", f"{name.title()} exige NEX {minimum}%.")
        requirements = {"armamento pesado": ("forca", 2), "combate defensivo": ("intelecto", 2),
                        "combater com duas armas": ("agilidade", 3), "reflexos defensivos": ("agilidade", 2),
                        "golpe demolidor": ("forca", 2), "sentido tatico": ("intelecto", 2)}
        for name, (attribute, minimum) in requirements.items():
            if name in powers and getattr(attrs, attribute) < minimum:
                error("powers", f"{name.title()} exige {attribute} {minimum}.")
        for name, skills in {"golpe demolidor": {"luta"}, "sentido tatico": {"percepcao", "tatica"},
                            "tiro certeiro": {"pontaria"}, "saque rapido": {"iniciativa"}}.items():
            if name in powers and not skills <= set(trained):
                error("powers", f"{name.title()} exige treinamento em {', '.join(sorted(skills))}.")
        if "transcender" in powers:
            if sheet.nex == 15 and derived:
                # Existe apenas uma oportunidade ordinária de poder de classe
                # neste recorte, no avanço para 15%; não retirar SAN de 5/10%.
                derived["san"] -= GROWTH[sheet.character_class][2]
                warnings.append("Transcender: descontado o ganho de SAN do avanço para NEX 15%. Confira o poder paranormal escolhido e seus pré-requisitos.")
            else:
                warnings.append("Transcender registrado fora do avanço de NEX 15%: origem da escolha e efeito na SAN exigem revisão; não foi aplicado automaticamente.")
        if sheet.character_class == "especialista" and norm(sheet.track) == "medico de campo" and "medicina" not in trained:
            error("track", "Médico de Campo exige treinamento em Medicina (PDF39). O uso das habilidades também requer kit de medicina.")
        if sheet.character_class == "ocultista":
            minimum_rituals = sheet.nex // 5 + 2
            if sheet.nex >= 10 and norm(sheet.track) == "graduado":
                minimum_rituals += 1
            if len(sheet.rituals) < minimum_rituals:
                error("rituals", f"Ocultista começa com 3 rituais e aprende 1 por avanço: registre pelo menos {minimum_rituals} em NEX {sheet.nex}%. Trilhas e poderes podem conceder adicionais.")
    categories = [0] * 5
    used = 0.0
    unknown_items = []
    reductions_used = set()
    for index, item in enumerate(sheet.equipment):
        if not item.name:
            error(f"equipment.{index}.name", "Preencha o nome do item ou remova a linha vazia.")
        categories[item.category] += item.quantity
        used += item.spaces * item.quantity
        known = ITEMS.get(item_key(item.name))
        if known and (item.category, item.spaces) != known[:2]:
            expected_category, expected_spaces, group = known
            explanation = norm(item.description)
            modifiers = []
            # Exceções implementadas exigem habilitação e alvo explícito. Não
            # interpretar qualquer descrição livre como autorização de redução.
            if (supported and sheet.nex >= 10 and sheet.character_class == "combatente"
                    and norm(sheet.track) == "aniquilador" and "a favorita" in powers
                    and "favorita" in explanation and group in {"simples", "tatica", "pesada"}):
                modifiers.append(("a favorita", 1, 0))
            if (norm(sheet.origin) == "engenheiro" and "ferramentas favoritas" in powers
                    and "ferramentas favoritas" in explanation and group not in {"simples", "tatica", "pesada"}):
                modifiers.append(("ferramentas favoritas", 1, 0))
            if (sheet.character_class == "especialista" and supported and sheet.nex >= 15
                    and "mochila de utilidades" in powers and "mochila de utilidades" in explanation
                    and group not in {"simples", "tatica", "pesada"}):
                modifiers.append(("mochila de utilidades", 1, 1))
            for modifier, category_reduction, space_reduction in modifiers:
                if modifier in reductions_used:
                    error(f"equipment.{index}", f"{modifier.title()} já foi aplicada a outro item; declare uma única escolha.")
                    continue
                reductions_used.add(modifier)
                expected_category = max(0, expected_category - category_reduction)
                expected_spaces = max(0, expected_spaces - space_reduction)
            if (item.category, item.spaces) != (expected_category, expected_spaces):
                error(f"equipment.{index}", f"{item.name}: esperados categoria {expected_category} e {expected_spaces} espaço(s) por unidade, conforme a tabela e reduções reconhecidas. Informe os valores corretos; modificações não implementadas exigem revisão separada.")
        if not known:
            unknown_items.append(item.name)
    limits = RANKS.get(norm(sheet.rank))
    if not limits:
        error("rank", "Informe uma patente oficial para conferir os limites de itens.")
    else:
        limits = list(limits)
        # Profissão concede um item adicional fora da requisição da Ordem
        # (PDF56), sem aumentar todas as categorias simultaneamente.
        profession_bonus = max((s.bonus for s in sheet.skills
                                if norm(s.name).split("(", 1)[0].strip() == "profissao"), default=0)
        if profession_bonus in (5, 10, 15):
            limits[profession_bonus // 5 - 1] += 1
        for category, limit in enumerate(limits, 1):
            if categories[category] > limit:
                error("equipment", f"{sheet.rank}: limite de {limit} item(ns) da categoria {category}; foram registrados {categories[category]}. Confira exceções de habilidades.")
    if derived:
        derived["load"] = round(used, 2)
        if used > derived["capacity"] * 2:
            error("equipment", "A carga registrada excede o dobro da capacidade.")
        elif used > derived["capacity"]:
            warnings.append("Sobrecarga: -5 em Defesa e perícias afetadas por carga; deslocamento -3 m.")
        # Possuir proteção não comprova que esteja vestida. O modelo de dados
        # ainda não guarda essa escolha, portanto não somar Defesa de inventário.
        if used > derived["capacity"]:
            derived["defense"] -= 5
    warnings.append("Validação parcial. PV/PE/SAN incluem a base da classe e apenas os efeitos implementados de Desgarrado, Vítima, Universitário, Cultista Arrependido e Transcender em NEX 15%. Outros poderes e efeitos precisam de revisão. Defesa é base com sobrecarga; não pressupõe que itens do inventário estejam vestidos.")
    warnings.append("Capacidade inclui Inventário Otimizado do Técnico; limites de patente incluem o item adicional de Profissão. Escolhas completas de origem/trilha/poderes, proficiências e rituais ainda precisam de revisão.")
    warnings.append("As categorias e espaços informados são somados; reduções por habilidades, proficiências e efeitos dos itens precisam ser conferidos nas fontes.")
    if unknown_items:
        warnings.append("Itens fora do catálogo de conferência: " + ", ".join(unknown_items) + ".")
        completion_issues.append({"field":"equipment", "message":"Faltam valores conferidos para os itens fora do catálogo; confira o livro e registre os dados manualmente."})
    item_names = {norm(item.name) for item in sheet.equipment}
    for weapon in sheet.equipment:
        ammunition = AMMUNITION.get(norm(weapon.name))
        if ammunition and norm(ammunition) not in item_names:
            message = f"{weapon.name}: não há {ammunition} no inventário. Inclua a munição necessária para usar a arma ou registre uma exceção da mesa."
            completion_issues.append({"field":"equipment", "message":message})
            warnings.append(message)
    ready_for_review = not errors and not completion_issues and supported
    return {"valid": not errors, "complete": False, "ready_for_review": ready_for_review, "full_sheet_validated": False,
            "errors": errors, "completion_issues": completion_issues, "warnings": warnings, "derived": derived,
            "scope": {"description": "Bases NEX 5/10/15, alguns efeitos de origem/trilha, treinamento, pré-requisitos selecionados, patente e carga; nenhuma ficha recebe aprovação completa automática.",
                      "full_sheet_validated": False},
            "sources": [{"title": "Ordem Paranormal RPG", "edition": "1.1", "pdf_page": page,
                         "printed_page": str(page - 8)} for page in
                        [22, 26, 27, 29, 30, 31, CLASS_RULES[sheet.character_class]["pdf_page"], 34, 39, 40, 48, 56, 60, 61, 64, 65, 70]]}


def sheet_payload(sheet: SheetData) -> tuple[dict, dict]:
    report = validate_sheet(sheet)
    return {**sheet.model_dump(), "derived": report["derived"]}, report
