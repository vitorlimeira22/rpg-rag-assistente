from rpg_assistente.character_evidence import character_evidence
from rpg_assistente.config import Settings
from rpg_assistente.db import connect
from rpg_assistente.dialogue import retrieval_query, task_context


def test_correction_keeps_requested_class_and_nex_without_trusting_previous_answer():
    history = [
        {"role": "user", "content": "Sugira uma ficha de combatente NEX 15 completa."},
        {"role": "assistant", "content": "O Especialista NEX 50 usa outra regra errada."},
    ]
    correction = "Tudo isso já está no material; escolha as opções e termine a proposta."
    task = task_context(correction, history)
    assert task["character_class"] == "combatente"
    assert task["nex"] == 15
    query = retrieval_query(correction, history)
    assert query.startswith("Criar personagem combatente NEX 15")
    assert "especialista" not in query.lower()


def test_character_task_survives_edits_but_does_not_contaminate_a_new_subject():
    history = [{"role": "user", "content": "Monte um ocultista NEX 10."}]
    assert task_context("Prefiro um especialista e NEX 15", history)["character_class"] == "especialista"
    assert task_context("Prefiro um especialista e NEX 15", history)["nex"] == 15
    assert task_context("O que é uma relíquia?", history) == {"intent": "consult"}
    assert task_context("Agora outro assunto: explique a entidade", history) == {"intent": "consult"}
    assert task_context("Sugira um poder de combatente", None) == {"intent": "consult"}


def test_review_and_creation_have_distinct_intents():
    assert task_context("Revise minha ficha de especialista NEX 5")["intent"] == "review_character"
    assert task_context("Sugira uma build de especialista NEX 15")["intent"] == "create_character"


def test_explicit_option_title_outweighs_incidental_mention(tmp_path):
    settings, conn = _fixture(tmp_path)
    try:
        rows = [
            (6,"TABELA 1.1: ORIGENS\n2-8\nInventor\nArtes e Crime\nIdeia\n9-10\nPesquisador\nCiências e Percepção\nAchado"),
            (7,"Inventor\nEste pesquisador conhece atributos, habilidades e investigação.\nPerícias Treinadas. Artes e Crime.\nIdeia. Um efeito fictício.\nPesquisador\nOutro conceito de origem.\nPerícias Treinadas. Ciências e Percepção.\nAchado. Um efeito fictício distinto."),
        ]
        for number,text in rows:
            page=conn.execute("INSERT INTO pages(book_id,pdf_page,printed_page,chapter,section,text) VALUES(1,?,?,?,?,?)",
                              (number,str(number),"Criação","Origens",text)).lastrowid
            conn.execute("INSERT INTO chunks(page_id,position,text) VALUES(?,?,?)",(page,0,text))
        conn.commit()
        result=character_evidence(conn,settings,character_class="combatente",nex=5,
                                  question="Crie ficha com origem Pesquisador, atributos, habilidades e investigação")
        assert result["candidate_options"]["origem"][0]["name"] == "Pesquisador"
    finally:
        conn.close()


def test_persisted_task_survives_history_window_and_resets_on_new_topic():
    previous = {"intent": "create_character", "character_class": "combatente", "nex": 15,
                "request": "Monte uma build de combatente NEX 15.", "invented_rule": "wrong"}
    current = task_context("Agora troque a arma e confira a carga", [], previous)
    assert current["character_class"] == "combatente"
    assert current["nex"] == 15
    assert "invented_rule" not in current
    assert "combatente NEX 15" in retrieval_query("Termine a ficha", [], previous)
    assert task_context("Qual o ritual mais poderoso?", [], previous) == {"intent": "consult"}


def _fixture(tmp_path):
    settings = Settings(tmp_path / "data.db", "", "", 0, 100, 10, None, None, False)
    conn = connect(settings.db_path)
    conn.execute(
        "INSERT INTO books(title,edition,pdf_path,sha256,page_count,imported_at) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)",
        ("Exemplo", "1", "demo.pdf", "synthetic", 5),
    )
    texts = [
        (1, "Atributos", "GERAÇÃO\nDE ATRIBUTOS\nDistribua pontos conforme o acordo.\nEXEMPLO\nTexto narrativo."),
        (2, "Combatente", "PONTOS DE VIDA INICIAIS\nFato de Combatente.\nPERÍCIAS TREINADAS\nEscolha uma."),
        (3, "Especialista", "PONTOS DE VIDA INICIAIS\nFato de Especialista que não deve contaminar."),
        (4, "Armas", "TABELA 3.3: ARMAS\nArma\nCategoria\nDano\nEspaços\nBastão\n0\n1d4\n1"),
        (5, "Combatente", "Guardião\nDescrição da trilha.\nNEX 10% - Guarda. Efeito fictício completo.\nNEX 40% - Outro. Efeito futuro."),
    ]
    for number, section, text in texts:
        page = conn.execute(
            "INSERT INTO pages(book_id,pdf_page,printed_page,chapter,section,text) VALUES(1,?,?,?,?,?)",
            (number, str(number), "Criação", section, text),
        ).lastrowid
        conn.execute("INSERT INTO chunks(page_id,position,text) VALUES(?,?,?)", (page, 0, text))
    conn.commit()
    return settings, conn


def test_evidence_uses_canonical_complete_units_and_declares_gaps(tmp_path):
    settings, conn = _fixture(tmp_path)
    try:
        result = character_evidence(conn, settings, character_class="combatente", nex=15)
        joined = "\n".join(row["text"] for row in result["sources"])
        assert "Fato de Combatente" in joined
        assert "Fato de Especialista" not in joined
        assert "Guarda. Efeito fictício completo." in joined
        assert "Efeito futuro" not in joined
        assert "Bastão\n0\n1d4\n1" in joined  # tabela integral, incluindo cabeçalho
        assert result["coverage"]["complete"] is False
        assert "origem" in result["coverage"]["missing"]
        for source in result["sources"]:
            page = conn.execute("SELECT text FROM pages WHERE pdf_page=?", (source["pdf_page"],)).fetchone()[0]
            assert source["text"] in page
    finally:
        conn.close()


def test_small_budget_never_cuts_rule_into_a_partial_prerequisite(tmp_path):
    settings, conn = _fixture(tmp_path)
    try:
        result = character_evidence(conn, settings, character_class="combatente", nex=15, max_chars=1000)
        assert sum(len(source["text"]) for source in result["sources"]) <= 1000
        assert all(not source["truncated"] for source in result["sources"])
        assert result["coverage"]["missing"]
    finally:
        conn.close()


def test_ritual_candidates_have_complete_descriptions_and_keep_upgrade_requirements(tmp_path):
    settings, conn = _fixture(tmp_path)
    try:
        for index, name in enumerate(("Brisa", "Véu", "Raiz"), 6):
            text = (f"{name}\n ENERGIA 1 \nExecução: padrão\nDuração: cena\n"
                    "Efeito fictício conferível.\nDiscente (+2 PE): outro efeito. Requer 2º círculo.\n"
                    "Próxima Entrada\n MORTE 2 \nExecução: padrão\nTexto que continua...")
            page = conn.execute("INSERT INTO pages(book_id,pdf_page,printed_page,chapter,section,text) VALUES(1,?,?,?,?,?)",
                                (index, str(index), "Paranormal", "Descrição dos Rituais", text)).lastrowid
            conn.execute("INSERT INTO chunks(page_id,position,text) VALUES(?,?,?)", (page, 0, text))
        conn.commit()
        result = character_evidence(conn, settings, character_class="ocultista", nex=5)
        assert result["coverage"]["ritual_candidates"] == 3
        assert {item["name"] for item in result["candidate_options"]["rituais"]} == {"Brisa", "Véu", "Raiz"}
        for item in result["candidate_options"]["rituais"]:
            source = result["sources"][item["citation_id"] - 1]
            assert "Requer 2º círculo." in source["text"]
            assert "Próxima Entrada" not in source["text"]
    finally:
        conn.close()
