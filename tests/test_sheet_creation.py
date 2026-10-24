from rpg_assistente.sheet_creation import complete_creation
from rpg_assistente.sheet_models import SheetData,validate_sheet


def test_specialist_creation_completes_invariants_without_relying_on_model_arithmetic():
    data=SheetData(character_class="especialista",nex=10,origin="Investigador",track="Técnico",
        attributes={"forca":1,"agilidade":2,"intelecto":3,"presenca":2,"vigor":1},
        skills=[{"name":"Perícia inventada","bonus":5}],powers=[],
        equipment=[{"name":"Pistola","category":1,"spaces":1},{"name":"Balas curtas","category":1,"spaces":0.2}])
    evidence={"sources":[{"citation_id":1,"text":"Investigador\nPerícias Treinadas. Investigação e Percepção.\nFaro para Pistas. Um efeito conferível."}],
              "candidate_options":{"origem":[{"name":"Investigador","citation_id":1}]}}
    result,notices=complete_creation(data,evidence)
    report=validate_sheet(result)
    assert report["errors"] == []
    assert len(result.skills) == 12
    assert {s.name for s in result.skills} >= {"Investigação","Percepção"}
    assert result.equipment[1].category == 0 and result.equipment[1].spaces == 1
    assert any(p.name.startswith("Perito (") for p in result.powers)
    assert any(p.name == "Faro para Pistas" for p in result.powers)
    assert notices


def test_creation_keeps_model_preferences_and_supplies_required_ammunition():
    data=SheetData(nex=10,origin="Acadêmico",track="Aniquilador",
        attributes={"forca":2,"agilidade":3,"intelecto":2,"presenca":1,"vigor":1},
        skills=[{"name":"Pontaria","bonus":5},{"name":"Reflexos","bonus":5}],
        equipment=[{"name":"Pistola","category":1,"spaces":1}])
    result,notices=complete_creation(data,{"sources":[],"candidate_options":{}})
    assert {s.name for s in result.skills} >= {"Pontaria","Reflexos"}
    assert len(result.skills)==7
    assert any(i.name == "Balas curtas" for i in result.equipment)
    assert validate_sheet(result)["completion_issues"] == []


def test_unsupported_progression_is_never_filled_as_if_supported():
    data=SheetData(nex=50)
    result,notices=complete_creation(data,{"sources":[],"candidate_options":{}})
    assert result.skills == [] and result.powers == [] and notices == []


def test_empty_inventory_gets_a_reviewable_initial_suggestion():
    data=SheetData(character_class="especialista",nex=10,origin="Investigador",track="Técnico",
        attributes={"forca":1,"agilidade":2,"intelecto":3,"presenca":2,"vigor":1})
    result,notices=complete_creation(data,{"sources":[],"candidate_options":{}})
    assert len(result.equipment) == 2
    assert all(item.category == 0 for item in result.equipment)
    assert any("inventário inicial" in notice for notice in notices)


def test_unknown_suggestion_does_not_become_category_zero_by_default():
    data=SheetData(equipment=[{"name":"Item não implementado"}])
    result,notices=complete_creation(data,{"sources":[],"candidate_options":{}})
    assert all(item.name != "Item não implementado" for item in result.equipment)
    assert any("valores presumidos" in notice for notice in notices)


def test_rank_budget_keeps_weapon_and_ammunition_before_optional_armor():
    data=SheetData(origin="Acadêmico",attributes={"forca":2,"agilidade":3,"intelecto":2,"presenca":1,"vigor":1},
        equipment=[{"name":"Fuzil de caça","category":1,"spaces":2},
                   {"name":"Proteção leve","category":1,"spaces":2}])
    result,notices=complete_creation(data,{"sources":[],"candidate_options":{}})
    names={i.name for i in result.equipment}
    assert names == {"Fuzil de caça","Balas longas"}
    assert validate_sheet(result)["completion_issues"] == []
    assert not any(e["field"] == "equipment" for e in validate_sheet(result)["errors"])


def test_favorite_reduction_is_applied_before_rank_budget():
    data=SheetData(nex=10,origin="Acadêmico",track="Aniquilador",
        attributes={"forca":2,"agilidade":3,"intelecto":2,"presenca":1,"vigor":1},
        equipment=[{"name":"Fuzil de assalto","category":2,"spaces":2}])
    result,notices=complete_creation(data,{"sources":[],"candidate_options":{}})
    assert any(i.name == "Fuzil de assalto" and i.category == 1 for i in result.equipment)
    assert any(p.name == "A Favorita (Fuzil de assalto)" for p in result.powers)
    assert not any(e["field"] == "equipment" for e in validate_sheet(result)["errors"])


def test_explicit_incompatible_equipment_is_reported_instead_of_silently_removed():
    data=SheetData(origin="Acadêmico",attributes={"forca":2,"agilidade":3,"intelecto":2,"presenca":1,"vigor":1},
        equipment=[{"name":"Fuzil de assalto","category":2,"spaces":2}])
    result,notices=complete_creation(data,{"sources":[],"candidate_options":{}},"Quero um Fuzil de assalto")
    assert any(i.name == "Fuzil de assalto" for i in result.equipment)
    assert any("categoria 2" in e["message"] for e in validate_sheet(result)["errors"])
