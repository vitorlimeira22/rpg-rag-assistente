"""Gera um livro fictício, criado para a demonstração pública do projeto."""

from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "demo_rules.pdf"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        pages = [
            (
                "Capitulo 1 - Testes",
                "Cronicas do Farol - regras de demonstracao, versao 0.1\n\n"
                "Para um teste de pericia, role um dado de vinte faces (1d20) e some "
                "o valor da pericia. O teste tem sucesso se o total for igual ou maior "
                "que a dificuldade definida pelo narrador.\n\n"
                "Uma pericia treinada concede +2. Sem treinamento, o bonus da pericia "
                "e zero. O narrador informa a dificuldade antes da rolagem.",
            ),
            (
                "Capitulo 2 - Energia",
                "Cada personagem comeca com 3 pontos de energia. Uma habilidade "
                "simples custa 1 ponto. Os pontos gastos retornam apos um descanso "
                "completo de oito horas.\n\n"
                "Este livro ficticio nao possui espadas laser nem regras de combate "
                "espacial. A ausencia de uma regra deve ser informada ao jogador.",
            ),
        ]
        for title, body in pages:
            page = doc.new_page(width=595, height=842)
            page.insert_text((55, 65), title, fontsize=20, fontname="helv")
            page.insert_textbox(pymupdf.Rect(55, 100, 540, 720), body,
                                fontsize=12, lineheight=1.5, fontname="helv")
            page.insert_text((55, 790), f"Pagina {len(doc)}", fontsize=10)
        doc.set_toc([[1, pages[0][0], 1], [1, pages[1][0], 2]])
        doc.save(OUTPUT)
    print(OUTPUT)
    print("Se substituir a demonstração distribuída, revise o conteúdo e atualize PUBLIC_DEMO_SHA256 em answer.py.")


if __name__ == "__main__":
    main()
