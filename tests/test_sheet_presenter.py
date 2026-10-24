from rpg_assistente.sheet_presenter import canonical_descriptions, render_update


def test_recognized_ability_uses_canonical_rule_instead_of_invented_summary():
    evidence = {"sources":[{"citation_id":1,"pdf_page":32,"text":
        "Ataque Especial. Um efeito verificável no livro.\nHabilidade de Trilha. Outra regra."}],
        "candidate_options":{}}
    data = {"powers":[{"name":"Ataque Especial (2 PE)","description":"Texto inventado"}],"rituals":[],"equipment":[]}
    refs = canonical_descriptions(data,evidence,{"powers"})
    assert "Texto inventado" not in data["powers"][0]["description"]
    assert data["powers"][0]["description"] == "Ataque Especial. Um efeito verificável no livro. (PDF p.32)"
    assert refs["ataque especial"] == 1


def test_weapon_description_preserves_table_and_does_not_invent_ammunition():
    evidence = {"sources":[{"citation_id":1,"pdf_page":64,"text":
        "TABELA\nPistola\nI\n1d12\n18\nCurto\nB\n1\nBalas curtas\n0\n—\n—\n—\n—\n1"}],"candidate_options":{}}
    data = {"powers":[],"rituals":[],"equipment":[{"name":"Pistola","description":"Inclui munição ilimitada"}]}
    canonical_descriptions(data,evidence,{"equipment"})
    description = data["equipment"][0]["description"]
    assert "munição ilimitada" not in description
    assert "dano 1d12" in description and "categoria I" in description
    assert "PDF p.64" in description


def test_unknown_rules_are_not_replaced_by_unrelated_book_passage():
    evidence = {"sources":[],"candidate_options":{}}
    data = {"powers":[{"name":"Regra da mesa", "description":"Escolha do grupo"}],"rituals":[],"equipment":[]}
    assert canonical_descriptions(data,evidence,{"powers"}) == {}
    assert data["powers"][0]["description"] == "Escolha do grupo"
