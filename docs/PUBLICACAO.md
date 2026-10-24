# Preparação para GitHub e portfólio

Revisão de 24/10/2026. O objetivo é publicar o **código de um projeto local de estudo**. Hospedagem pública da aplicação exige outro trabalho de segurança e não faz parte desta entrega.

## O que foi preparado

- Repositório Git próprio nesta pasta, sem importar o histórico ou os outros projetos da pasta pai. Branch inicial: `codex/publication-ready`.
- `.gitignore` para ambientes, variantes de `.env`, bancos e seus arquivos auxiliares, logs, caches, modelos, exportações e PDFs privados. O único PDF público permitido é a demonstração autoral.
- Scanner de publicação que confere também o índice Git e não imprime valores de credenciais. Hook local para impedir commits inseguros e CI com a mesma checagem.
- `uv.lock`, instruções de instalação reproduzível, testes Python/JavaScript e avaliação pública isolada. Nenhuma chave precisa ser configurada no GitHub Actions.
- Telemetria do Chroma desativada nas instâncias usadas pelo projeto. A exceção para enviar o PDF de demonstração ao Groq exige caminho e hash do conteúdo revisado; renomear um livro privado não o torna público.
- Documentação de dados enviados, limitações, dependências e licença. Caminho pessoal removido do dossiê; capturas históricas conferidas visualmente, sem páginas do livro ou dados de jogadores.

O `.env` preenchido e os dados locais são preservados. Configurações reais de uso não são copiadas para `.env.example`. Este guia não autoriza redistribuir o livro, o formulário ou os dados extraídos deles.

## Resultado da revisão local

Validação feita em uma cópia contendo somente os arquivos preparados no Git, sem `.env` privado, usando Python 3.12.14/Windows e as dependências de `uv.lock`:

| Conferência | Resultado |
| --- | --- |
| Testes Python | 469 aprovados |
| Testes JavaScript | 14 aprovados |
| Fontes esperadas na demonstração autoral | 5/5; recuperação textual, sem avaliar geração |
| Instalação e consistência das dependências | Instalação separada concluída; `pip check` sem conflitos |
| Interface e HTTP | Página inicial disponível; caminhos privados retornam 404; host/origem externos bloqueados |
| Scanner de publicação | 107 arquivos conferidos, sem ocorrências nos padrões verificados |
| Materiais preparados no índice | Somente um PDF: `examples/demo_rules.pdf`; sem banco, chave, livro ou formulário privado |
| Auditoria de dependências | 126 pacotes avaliados; quatro alertas únicos conhecidos do Chroma, com análise/exceções em `SECURITY.md`; nenhum outro achado nessa execução |
| Dados pessoais | Conteúdo do `.env` preservado, confirmado por hash antes/depois |
| Consumo de IA nesta revisão | Nenhuma chamada real ao Groq |

A CI foi preparada, mas ainda não executada no GitHub. Não houve commit, associação a remoto ou push. Os testes comprovam os cenários executados, não uma garantia geral de segurança ou de precisão da IA. A pasta `data/publication/` contém a cópia isolada e um ZIP de conferência, ambos ignorados pelo Git.

## Verificação antes do primeiro envio

Execute na **pasta do projeto**, não na pasta que reúne seus demais projetos:

```powershell
git rev-parse --show-toplevel
git status --short
uv run --no-sync python scripts/check_publication.py
git add .
uv run --no-sync python scripts/check_publication.py --staged
git diff --cached --check
git diff --cached --stat
```

O primeiro comando deve apontar para a pasta `rpg-rag-assistente`. O `.env`, `data/`, `tmp/`, `output/`, o livro original e o formulário não devem aparecer entre os arquivos preparados. `git status --ignored --short` pode mostrá-los com `!!`, o que significa ignorados. Não use `git add -f` para esses arquivos. Ignore o `.git` da pasta pai ao publicar este projeto.

O hook já foi habilitado nesta cópia. Em outro clone, habilite novamente:

```powershell
git config core.hooksPath .githooks
```

O hook não é uma proteção absoluta: pode ser desativado e não substitui conferência de histórico e artefatos.

## Validação reproduzível

```powershell
uv sync --locked --extra test --group audit
uv run --no-sync pytest -q
node --test tests/frontend.test.cjs
uv run --no-sync python evaluation/run_demo.py
uv run --no-sync python scripts/check_dependencies.py
```

Node.js 22 é necessário apenas para os testes JavaScript; não há `npm install` nem build de frontend. Os testes usam dados sintéticos e transportes simulados, sem gastar cota de IA. `run_demo.py` importa o PDF autoral em um banco temporário e verifica fontes por página, sem ler `.env`, baixar modelos ou substituir o banco pessoal. O gabarito em `demo-questions.json` também serve para conferir manualmente respostas de uma API configurada; o script não avalia fidelidade da geração ou abstenção.

`check_dependencies.py` consulta uma base pública usando nomes e versões de pacotes. Os quatro alertas conhecidos do Chroma continuam documentados e visíveis, com exceções restritas ao uso embutido e revisão até 24/11/2026. Isso não equivale a “zero vulnerabilidades”; veja [SECURITY.md](../SECURITY.md).

## Primeiro envio — ação do proprietário

Crie no GitHub um repositório vazio. Evite adicionar outro README/licença pelo site antes de enviar estes arquivos. Depois de revisar o conteúdo preparado, faça o primeiro commit e associe o endereço fornecido pelo GitHub:

```powershell
git commit -m "Publica versão inicial do assistente de RPG"
git remote add origin ENDERECO_DO_SEU_REPOSITORIO
git push -u origin codex/publication-ready
```

Substitua o endereço pelo seu; não coloque chave de API ou token na URL. Use o login normal do GitHub/Git Credential Manager. A branch inicial pode ser escolhida como padrão no GitHub. Nenhum commit, repositório remoto ou push é realizado por este guia.

No GitHub, confira a primeira execução da CI e habilite os recursos de secret scanning, push protection e alertas de dependências disponíveis. O arquivo de Dependabot propõe atualizações; cada alteração continua precisando passar pelos testes. Configurações de conta/repositório não são ativadas automaticamente pelos arquivos locais.

## Como apresentar o projeto

Apresente como projeto de aprendizado com RAG híbrido, embeddings locais, banco vetorial, backend e validações determinísticas em Python. Explique por que SQLite/Chroma foram suficientes para um usuário e por que cálculos de ficha não são delegados somente à LLM. Mostre a separação entre fontes oficiais, regras da mesa e conteúdo criativo.

Informe o recorte: um sistema/edição, regras de ficha parcialmente conferidas em NEX 5/10/15, um livro ativo e respostas sujeitas a erros. Não afirme que o assistente conhece todo o universo, valida qualquer ficha ou aceita qualquer sistema com a mesma cobertura. Testes automatizados aprovados verificam os cenários implementados; não representam uma taxa geral de acerto da IA.

Use o PDF autoral e dados fictícios em vídeos, prints e demonstrações. As imagens históricas já incluídas ilustram a identidade visual anterior ao chat, sem demonstrar a interface mais recente. Não capture o `.env`, caminhos pessoais, console com credenciais, páginas do livro ou conversas reais de jogadores.

O código e os materiais autorais foram preparados sob AGPL-3.0-only considerando a dependência PyMuPDF. Leia [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) antes de publicar ou alterar a licença. O envio ao GitHub e o post no LinkedIn permanecem a cargo do proprietário.
