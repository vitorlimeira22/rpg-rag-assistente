"""Conferências mecânicas: progressão, exceções e ausência de aprovação global."""
import pytest

from rpg_assistente.sheet_models import Equipment, SheetData, validate_sheet


def example(character_class="combatente", nex=15, **changes):
    defaults = {
        "combatente": ("Aniquilador", ["Ataque Especial (2 PE, +5)", "A Favorita (faca)", "Golpe Pesado"], 6),
        "especialista": ("Técnico", ["Eclético", "Perito (Investigação, Tecnologia)", "Inventário Otimizado"], 10),
        "ocultista": ("Conduíte", ["Escolhido pelo Outro Lado", "Ampliar Ritual", "Ritual Potente"], 8),
    }
    track, powers, count = defaults[character_class]
    skill_names = ["Luta", "Fortitude", "Ocultismo", "Vontade", "Profissão", "Investigação",
                   "Tecnologia", "Reflexos", "Pontaria", "Tática", "Medicina", "Intuição"]
    payload = {
        "name": "Agente", "character_class": character_class, "nex": nex, "origin": "Operário",
        "track": track if nex >= 10 else "", "rank": "Recruta",
        "attributes": {"agilidade": 2, "forca": 3, "intelecto": 1, "presenca": 1, "vigor": 2},
        "skills": [{"name": name, "bonus": 5} for name in skill_names[:count]],
        "powers": [{"name": name} for name in powers],
        "equipment": [{"name": "Faca", "category": 0, "spaces": 1}],
        "rituals": [{"name": f"Ritual {i}"} for i in range(nex // 5 + 2)] if character_class == "ocultista" else [],
    }
    payload.update(changes)
    return SheetData.model_validate(payload)


@pytest.mark.parametrize("character_class,expected", [
    ("combatente", [(22, 3, 12), (28, 6, 15), (34, 9, 18)]),
    ("especialista", [(18, 4, 16), (23, 8, 20), (28, 12, 24)]),
    ("ocultista", [(14, 5, 20), (18, 10, 25), (22, 15, 30)]),
])
def test_class_progression_bases(character_class, expected):
    for nex, totals in zip((5, 10, 15), expected):
        report = validate_sheet(example(character_class, nex))
        derived = report["derived"]
        assert tuple(derived[key] for key in ("pv", "pe", "san")) == totals
        assert derived["pe_per_turn"] == nex // 5
        assert not report["full_sheet_validated"]
        assert not report["complete"]


@pytest.mark.parametrize("origin,key,expected", [
    ("Desgarrado", "pv", 37), ("Vítima", "san", 21),
    ("Universitário", "pe", 11), ("Cultista Arrependido", "san", 12),
])
def test_simple_origin_effects(origin, key, expected):
    report = validate_sheet(example(origin=origin))
    assert report["derived"][key] == expected
    if origin == "Universitário":
        assert report["derived"]["pe_per_turn"] == 4


def test_transcender_removes_only_the_selected_level_sanity_gain():
    sheet = example(powers=[{"name": "Ataque Especial"}, {"name": "A Favorita"},
                            {"name": "Transcender (Sangue de Ferro)"}])
    report = validate_sheet(sheet)
    assert report["derived"]["san"] == 15
    assert any("Transcender" in warning for warning in report["warnings"])
    earlier = validate_sheet(sheet.model_copy(update={"nex": 10}))
    assert earlier["derived"]["san"] == 15  # no class power gain yet


def test_technician_capacity_prevents_false_overload():
    sheet = example("especialista", 10,
                    attributes={"agilidade": 2, "forca": 1, "intelecto": 3, "presenca": 2, "vigor": 1},
                    equipment=[{"name": "Faca", "quantity": 12, "category": 0, "spaces": 1}])
    report = validate_sheet(sheet)
    assert report["derived"]["capacity"] == 20
    assert report["derived"]["load"] == 12
    assert report["derived"]["defense"] == 12
    assert not any("carga" in error["message"] for error in report["errors"])


def test_profession_grants_only_one_additional_item_in_appropriate_category():
    items = [{"name": "Pistola", "quantity": 3, "category": 1, "spaces": 1}]
    sheet = example(equipment=items)
    report = validate_sheet(sheet)
    assert not any(error["field"] == "equipment" for error in report["errors"])
    no_profession = sheet.model_copy(update={"skills": [s for s in sheet.skills if s.name != "Profissão"]})
    assert any("limite de 2" in error["message"] for error in validate_sheet(no_profession)["errors"])


def test_armor_in_inventory_does_not_mean_worn_armor():
    sheet = example(equipment=[{"name": "Proteção leve", "category": 1, "spaces": 2}])
    assert validate_sheet(sheet)["derived"]["defense"] == 12


def test_weapon_without_ammunition_is_a_completion_issue_not_an_illegal_draft():
    sheet = example(equipment=[{"name":"Pistola", "category":1, "spaces":1}])
    report = validate_sheet(sheet)
    assert any("Balas curtas" in issue["message"] for issue in report["completion_issues"])
    assert report["ready_for_review"] is False
    supplied = sheet.model_copy(update={"equipment":[*sheet.equipment,
        Equipment(name="Balas curtas",category=0,spaces=1)]})
    assert validate_sheet(supplied)["completion_issues"] == []


def test_nonexistent_track_and_missing_track_power_are_errors():
    assert any(error["field"] == "track" for error in validate_sheet(example(track="Armamento Pesado"))["errors"])
    result = validate_sheet(example(powers=[{"name": "Ataque Especial"}, {"name": "Golpe Pesado"}]))
    assert any("Favorita" in error["message"] for error in result["errors"])


def test_wrong_known_item_categories_are_not_silently_accepted():
    sheet = example(equipment=[{"name": "Pistola", "category": 0, "spaces": 1,
                                "description": "Arma de categoria I"}])
    result = validate_sheet(sheet)
    assert any(error["field"] == "equipment.0" for error in result["errors"])
    assert sheet.equipment[0].category == 0  # do not rewrite user/model input


def test_favorite_requires_eligible_track_power_and_explicit_target():
    item = {"name": "Espada", "category": 0, "spaces": 1, "description": "Arma favorita"}
    assert not any(error["field"] == "equipment.0" for error in validate_sheet(example(equipment=[item]))["errors"])
    wrong_track = validate_sheet(example(track="Guerreiro", equipment=[item]))
    assert any(error["field"] == "equipment.0" for error in wrong_track["errors"])
    two_items = validate_sheet(example(equipment=[item, {"name": "Pistola", "category": 0, "spaces": 1, "description": "Favorita"}]))
    assert any("outro item" in error["message"] for error in two_items["errors"])


def test_decorated_power_names_cannot_bypass_prerequisites():
    result = validate_sheet(example(powers=[{"name": "Ataque Especial (2PE,+5)"}, {"name": "A Favorita"},
                                           {"name": "Proteção Pesada (usar colete)"}]))
    assert any("NEX 30%" in error["message"] for error in result["errors"])


def test_training_bonus_is_not_total_bonus_and_unknown_skills_are_rejected():
    result = validate_sheet(example(skills=[{"name": "Luta", "bonus": 7}, {"name": "Voar", "bonus": 5}]))
    assert any(error["field"] == "skills.0.bonus" for error in result["errors"])
    assert any(error["field"] == "skills.1.name" for error in result["errors"])


def test_unsupported_evolution_does_not_apply_initial_attribute_limits():
    result = validate_sheet(example(nex=20, attributes={"agilidade": 2, "forca": 4, "intelecto": 1, "presenca": 1, "vigor": 2}))
    assert not result["derived"]
    assert not any(error["field"].startswith("attributes") for error in result["errors"])
    assert not result["ready_for_review"]
