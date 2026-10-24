from copy import deepcopy

import pytest
from pydantic import ValidationError

from rpg_assistente.characters import CharacterInput, character_options, validate_character


@pytest.fixture
def basic_character() -> dict:
    return {
        "name": "Agente de demonstração",
        "character_class": "combatente",
        "nex": 5,
        "attributes": {"agilidade": 2, "forca": 2, "intelecto": 2, "presenca": 2, "vigor": 1},
    }


@pytest.mark.parametrize(
    "character_class,pv,pe,san,pdf_page,printed_page",
    [
        ("combatente", 21, 4, 12, 33, "25"),
        ("especialista", 17, 5, 16, 37, "29"),
        ("ocultista", 13, 6, 20, 41, "33"),
    ],
)
def test_class_bases_and_verifiable_references(
    basic_character, character_class, pv, pe, san, pdf_page, printed_page
):
    basic_character["character_class"] = character_class
    result = validate_character(basic_character)
    assert result["valid"]
    assert result["errors"] == []
    assert result["derived"] == {
        "pv_base": pv, "pe_base": pe, "san_base": san, "defesa_base": 12,
        "limite_pe_base": 1, "attribute_total": 9,
    }
    assert any(s["pdf_page"] == pdf_page and s["printed_page"] == printed_page for s in result["sources"])
    assert result["scope"]["edition"] == "1.1"
    assert result["scope"]["full_sheet_validated"] is False


def test_one_reduced_attribute_reallocates_point_without_increasing_total(basic_character):
    basic_character["attributes"] = {
        "agilidade": 2, "forca": 0, "intelecto": 3, "presenca": 3, "vigor": 1,
    }
    assert validate_character(basic_character)["valid"]

    # Erroneously treating the optional reduction as a free extra point would
    # produce 10 instead of 9 and must not be accepted.
    basic_character["attributes"]["agilidade"] = 3
    invalid = validate_character(basic_character)
    assert not invalid["valid"]
    assert invalid["derived"] is None
    assert any(e["field"] == "attributes" for e in invalid["errors"])


def test_two_zero_attributes_are_rejected_even_with_correct_total(basic_character):
    basic_character["attributes"] = {
        "agilidade": 0, "forca": 0, "intelecto": 3, "presenca": 3, "vigor": 3,
    }
    result = validate_character(basic_character)
    assert not result["valid"]
    assert any("no máximo um" in e["message"] for e in result["errors"])


@pytest.mark.parametrize("attributes", [
    {"agilidade": 4, "forca": 1, "intelecto": 1, "presenca": 1, "vigor": 2},
    {"agilidade": -1, "forca": 3, "intelecto": 3, "presenca": 2, "vigor": 2},
    {"agilidade": 1, "forca": 1, "intelecto": 1, "presenca": 1, "vigor": 1},
])
def test_invalid_distribution_does_not_produce_derived_values(basic_character, attributes):
    basic_character["attributes"] = attributes
    result = validate_character(basic_character)
    assert not result["valid"]
    assert result["errors"]
    assert result["derived"] is None


@pytest.mark.parametrize("value", [True, False, 2.0, 1.5, "2", None])
def test_attribute_values_are_not_silently_coerced(basic_character, value):
    basic_character["attributes"]["agilidade"] = value
    result = validate_character(basic_character)
    assert not result["valid"]
    assert result["errors"][0]["field"] == "attributes.agilidade"
    with pytest.raises(ValidationError):
        CharacterInput.model_validate(basic_character)


@pytest.mark.parametrize("nex", [0, 10, 50, 99, 100])
def test_nex_outside_creation_scope_is_not_approved(basic_character, nex):
    basic_character["nex"] = nex
    result = validate_character(basic_character)
    assert not result["valid"]
    assert result["derived"] is None
    assert any(e["field"] == "nex" for e in result["errors"])


def test_zero_vigor_and_zero_presence_use_actual_value(basic_character):
    basic_character["attributes"] = {
        "agilidade": 3, "forca": 2, "intelecto": 2, "presenca": 2, "vigor": 0,
    }
    assert validate_character(basic_character)["derived"]["pv_base"] == 20
    basic_character["attributes"].update(presenca=0, vigor=2)
    assert validate_character(basic_character)["derived"]["pe_base"] == 2


@pytest.mark.parametrize("origin", [
    "Cultista Arrependido", "Desgarrado", "Universitário", "Vítima", "Policial",
])
def test_origin_is_explicitly_not_included_in_base_stats(basic_character, origin):
    basic_character["origin"] = origin
    result = validate_character(basic_character)
    assert result["valid"]
    assert result["scope"]["full_sheet_validated"] is False
    assert set(result["derived"]) == {
        "pv_base", "pe_base", "san_base", "defesa_base", "limite_pe_base", "attribute_total",
    }
    assert result["derived"]["san_base"] == 12
    assert any("origem" in warning and "não foram aplicados" in warning for warning in result["warnings"])


def test_names_and_notes_are_bounded_and_extra_fields_rejected(basic_character):
    for update in ({"name": "   "}, {"name": "x" * 81}, {"origin": "x" * 121},
                   {"notes": "x" * 2001}, {"pv_base": 900}, {"character_class": "mago"}):
        result = validate_character({**basic_character, **update})
        assert not result["valid"]
        assert result["derived"] is None

    basic_character["attributes"]["destreza"] = 2
    assert not validate_character(basic_character)["valid"]


def test_model_and_dict_have_same_results_without_mutating_input(basic_character):
    original = deepcopy(basic_character)
    assert validate_character(CharacterInput.model_validate(basic_character)) == validate_character(basic_character)
    assert basic_character == original


def test_options_cannot_mutate_rule_metadata():
    first = character_options()
    assert first["attribute_rules"]["total"] == 9
    assert first["attribute_rules"]["max_reduced"] == 1
    assert first["nex_values"] == [5]
    first["scope"]["excluded"].clear()
    first["classes"][0]["label"] = "Alterada"
    second = character_options()
    assert second["scope"]["excluded"]
    assert second["classes"][0]["label"] == "Combatente"


def test_pe_limit_exception_is_visible_in_every_valid_report(basic_character):
    result = validate_character(basic_character)
    assert result["derived"]["limite_pe_base"] == 1
    assert any("custo mínimo" in warning for warning in result["warnings"])
