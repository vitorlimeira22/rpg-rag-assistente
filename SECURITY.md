# Segurança e privacidade

Este é um aplicativo pessoal para `127.0.0.1`, sem autenticação multiusuário. Publicar o código no GitHub não exige expor o servidor. Não use túnel público, `0.0.0.0` ou hospedagem compartilhada com dados reais sem uma revisão própria de implantação.

## Dados e conexões

| Informação/operação | Destino |
| --- | --- |
| PDFs, texto extraído, embeddings, fichas, campanhas, conversas e registros de uso | Arquivos locais em `data/` e caminhos configurados |
| Chave Groq | `.env`/ambiente do backend; autenticação HTTPS com Groq |
| Geração de resposta | Pergunta, fontes selecionadas, histórico limitado e contexto da tarefa vão ao Groq |
| Chat de ficha | Também envia o estado atual da ficha e as evidências selecionadas |
| Preparação de embeddings/reranker | Baixa os modelos; inferência posterior local, sem enviar o texto do livro ao serviço de modelos |
| Instalação/auditoria | PyPI e base pública de vulnerabilidades recebem nomes/versões dos pacotes |

O PDF completo não é anexado à chamada de geração. Trechos selecionados podem conter informação privada. `ALLOW_PRIVATE_BOOK_TO_GROQ=false` impede a geração com livro privado; a exceção é o PDF autoral de demonstração conferido pelo código. Habilite somente com material e contexto que você aceita compartilhar com o provedor. A política do provedor é independente do projeto: [Groq — Your Data](https://console.groq.com/docs/your-data).

Chroma é usado com `PersistentClient`, sem servidor HTTP, e com `anonymized_telemetry=False`. Não configure tracing externo, como LangSmith, com conteúdo privado sem avaliar o envio adicional. O projeto não precisa de chave LangChain/LangSmith. `/docs` e `/redoc` usam os recursos de documentação padrão do FastAPI, carregados de CDN; a interface principal usa arquivos locais e fontes de sistema.

## Proteções implementadas e limites

- Somente a pasta `static/` é servida. `.env`, bancos e PDFs não são diretórios públicos; fontes são acessadas por identificadores e a página original exige hash correspondente.
- Hosts locais, checagem de origem para escrita, JSON de até 32 KiB, CSP na interface, cabeçalhos contra interpretação indevida e cache desativado nas APIs.
- Conteúdo da IA é renderizado como texto/Markdown restrito; links de fontes são construídos pela aplicação.
- Ferramentas do modelo limitam consultas ao livro e conferências de regras suportadas. Não há execução de comandos, abertura de caminhos arbitrários ou navegação externa controlada pela IA.
- Limites de chamadas, tokens, tempo e pesquisa são configuráveis; a cota local não substitui as cotas e cobranças do Groq.
- Erros externos são resumidos sem devolver corpo da resposta do provedor, chave ou caminhos locais. Isso não torna seguro digitar segredos no chat: pedidos e histórico são dados persistidos.

Dados locais não são criptografados pelo aplicativo. Proteja a conta do sistema e seus backups. Prompts não eliminam prompt injection nem garantem fidelidade às regras; use somente PDFs confiáveis e confira as fontes. Outros processos do mesmo computador podem acessar o serviço local.

## Dependências — revisão de 24/10/2026

A auditoria de `uv.lock` encontrou quatro alertas únicos para Chroma 1.5.9, sem versão corrigida listada: [CVE-2026-45829](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c), [CVE-2026-45833](https://github.com/advisories/GHSA-36p7-vc44-83pf), [CVE-2026-45831](https://github.com/advisories/GHSA-xph7-9rjv-w5fr) e [CVE-2026-45830](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr). A análise de aplicabilidade considera que os endpoints de servidor, modelos remotos via HTTP e autorização entre tenants descritos nesses alertas não são oferecidos por esta aplicação.

As exceções estão em [dependency-exceptions.json](evaluation/dependency-exceptions.json), vinculadas ao pacote e à versão exatos, **válidas até 24/11/2026**. Isso não corrige o Chroma nem autoriza expô-lo como serviço. A auditoria continua exibindo esses alertas e falha para novos achados ou revisão expirada. Reavalie ao mudar a arquitetura ou atualizar o pacote.

## Antes de enviar código

```powershell
.\.venv\Scripts\python.exe scripts/check_publication.py
git add .
.\.venv\Scripts\python.exe scripts/check_publication.py --staged
git diff --cached --stat
```

O scanner verifica caminhos, padrões de credenciais e correspondências com a chave local sem imprimi-la. `--staged` lê os bytes do índice, mesmo quando a cópia de trabalho mudou depois de `git add`. Não é uma garantia de detecção de todos os segredos, nem analisa todo o histórico Git. Este repositório foi iniciado sem herdar o histórico da pasta pai.

Para bloquear commits locais nessas condições, ative o hook versionado: `git config core.hooksPath .githooks`. Novos clones precisam ativá-lo novamente. A CI repete a verificação. No GitHub, habilite secret scanning/push protection e os alertas de dependências disponíveis para sua conta; essas configurações não são ativadas pelo código.

## Se houver exposição

Revogue e substitua a chave no provedor; apagar o arquivo do último commit não remove versões anteriores. Verifique o histórico e os artefatos antes de republicar. Não inclua credenciais, páginas de livros ou dados de jogadores em issues públicas. Para uma vulnerabilidade, use o canal privado do mantenedor ou “Report a vulnerability” se estiver habilitado no repositório.
