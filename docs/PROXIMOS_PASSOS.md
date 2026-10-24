# Revisão e próximos passos — 20/10/2026

Atualização: a [revisão final de 21/10](REVISAO_FINAL.md) reúne as correções e os
resultados atuais, incluindo falhas persistentes e o reteste interrompido por
HTTP 429. O restante deste documento preserva o histórico anterior.

## Decisão

Manter FastAPI, SQLite, Chroma, embeddings locais, busca híbrida, reranker e
integração Groq. Essa estrutura é proporcional a um livro e um usuário.
Não há evidência de que migrar para pgvector, adicionar agentes ou trocar de
framework resolva os erros observados. A prioridade é entregar evidências
completas e avaliar a fidelidade da resposta.

## O que os testes mostraram

Mesmo prompt v11, GPT-OSS-120B em `medium`, três perguntas, uma execução por condição.
“Referência” significa trechos canônicos escolhidos manualmente, com condições
e continuações; não é recuperação automática nem resposta pronta.

| Pergunta | Busca atual + uma chamada | Fontes de referência + uma chamada |
|---|---|---|
| Ritual mais poderoso | Não encontra descrições e generaliza benefício de uma trilha | Compara candidatos, mas erra cálculo e condições; conclusão não sustentada |
| Proteções para conjurar | Atribui modificação à proteção básica e confunde o teste de concentração | Melhora, mas inventa proporção do inventário e omite resistência e consequência da falha |
| Recuperação no interlúdio | Informa 3,5 e não encontra frequência; contexto incompleto | Acerta 3/7/14/21, arredondamento e uma vez por interlúdio; omite disponibilidade |

**Conclusão:** existem problemas de recuperação e de interpretação. O modelo
consegue resolver o caso de descanso com o contexto adequado; ainda erra outras
comparações mesmo recebendo as fontes necessárias. Não está demonstrado que
seja incapaz de atender ao projeto, nem que seja confiável para todas as regras.

Qwen3.8-27B foi testado na pergunta de rituais com exatamente o mesmo contexto:
organizou melhor os critérios, mas errou uma condição de aprimoramento de Fim
Inevitável. Uma resposta não justifica migrar. A comparação das outras duas
perguntas não foi concluída por 429; a última recusa indicou tokens por minuto.
Isso não é erro factual do modelo.

Nesta rodada: **9 tentativas, 7 respostas, 2 recusas 429**.
Uso conhecido: **13.766 tokens de entrada + 7.030 de saída**.
Não atribuir consumo zero às recusas sem informação do provedor.
O limite local foi elevado para 1.000 conforme autorização; a cota do Groq
continua independente. A rodada foi encerrada após o pedido de economizar uso.

Limitações da avaliação: amostra pequena, sem repetições, um caso já usado na
calibração. `answered` verifica referências numéricas, não correção factual.
Os testes diretos não medem a qualidade final do fluxo com ferramentas.

## Duas correções aplicadas

- **Prévia útil:** a busca agora mostra uma janela próxima aos termos
  encontrados, preservando o texto original e o limite de 400 caracteres.
  “Fim Inevitável” passou de aparecer em 0/3 para 3/3 prévias recuperadas.
  Isso comprova localização melhor; não comprova resposta correta.
- **Preservação de fontes:** o catálogo complementa as fontes iniciais e não
  as descarta. Ambas ficam visíveis desde a primeira rodada. A pergunta sobre
  proteções já encontrava as páginas certas, que eram substituídas por uma
  lista de rituais.

**65 testes focados passaram**, sem API. Prompt/fluxo identificado como v12.
As correções não foram reavaliadas com geração real nesta rodada.
O orçamento acumulado de contexto e a pesquisa obrigatória continuam limitações.

## Ordem recomendada

1. **Recuperar a regra completa.** Buscar trechos pequenos e expandir para a
   descrição correspondente, incluindo continuações e títulos. Isso é
   *parent retrieval*: localizar com precisão e entregar contexto suficiente.
   Páginas completas são uma aproximação; podem conter regras não relacionadas.
2. **Simplificar a pesquisa.** Avaliar uma resposta direta quando há evidência
   suficiente e pesquisa adicional para lacunas. Evitar exigir ferramentas em
   toda pergunta. Separar contexto de navegação das fontes necessárias à síntese
   para não gastar o orçamento com catálogos e duplicações.
3. **Validar antes de trocar o modelo.** Reusar os três casos, depois acrescentar
   perguntas novas. Conferir condições, referências e conclusão. Comparar outro
   modelo com fontes idênticas e repetir os casos decisivos.
4. **Depois ampliar fichas e missões.** Cálculos suportados permanecem em Python;
   saídas estruturadas ajudam a validar campos, mas não garantem regras corretas.
   Expandir além de NEX 5% somente com fórmulas, exemplos e testes conferidos.

Critério para adotar uma mudança: melhorar respostas verificadas, sem introduzir
erro crítico novo, registrando chamadas, tokens e tempo. Mais testes unitários
ou mais ferramentas, isoladamente, não demonstram melhor RAG.

## Referências consultadas

- [LangChain: arquiteturas de RAG](https://docs.langchain.com/oss/python/deepagents/retrieval):
  controle e previsibilidade do fluxo em duas etapas versus flexibilidade do agente.
- [LangChain: avaliação de RAG](https://docs.langchain.com/langsmith/evaluate-rag-tutorial):
  separar correção, relevância, fidelidade às fontes e qualidade da recuperação.
- [OpenAI: GPT-OSS-120B](https://developers.openai.com/api/docs/models/gpt-oss-120b):
  raciocínio configurável e ferramentas são capacidades, não garantia de precisão.
- [Groq: limites](https://console.groq.com/docs/rate-limits):
  a cota pública gratuita de 8.000 tokens/minuto é distinta da janela de contexto;
  os limites efetivos dependem da conta.

Para repetir o experimento, `evaluation/compare_contexts.py` prepara contextos
sem API por padrão; `--live` autoriza uma execução real. As fontes privadas ficam
em `data/evaluations/`, ignorado pelo Git. `reference-contexts.json` guarda apenas
delimitadores e hashes para conferir o material, sem fornecer o gabarito à LLM.

### Correção pontual posterior — v13

O usuário encontrou HTTP 400 na quarta chamada de “quais as criaturas mais fortes
por elemento?”. GPT-OSS-120B/medium permaneceu configurado. Corrigida a síntese:
recebe todas as fontes e suas citações, sem ordens de pesquisar nem histórico de
ferramentas indisponíveis. 35 testes focados passaram. Uma execução real concluiu
sem 400 (4 chamadas, 85,5 s, dos quais 62,4 s de espera pela cota). A resposta ainda
ficou incompleta por elemento e não comprova o ranking pedido; a qualidade da
comparação continua pendente. O corpo do erro anterior não foi preservado, então
não é possível confirmar sua causa exata retrospectivamente.

## Revisão do chat e comparações — v14

**Entrega:** conversas em SQLite com fontes preservadas por mensagem; até oito
mensagens/6.000 caracteres enviados como contexto; UUID por envio evita geração
duplicada. Modo e campanha vinculados à conversa. Troca do livro impede misturar
históricos e imagens. GPT-OSS-120B/medium mantido.

**Correção principal:** top-k não calcula máximos globais. O perfil Ordem 1.1
extraiu 49 fichas em 330 páginas, sem candidatos pendentes. Maior VD por elemento
principal: Diabo, Deus da Morte, Máscara do Desespero e Anfitrião, 400;
Degolificada/Medo, 320. Fontes: PDF 214/238/262/286/290; definição de VD em 186.
Magistrada está na narrativa 261. Nomes, páginas e valores são extraídos do banco,
não respostas fixadas no código. Cobertura significa fichas detectáveis pelo
formato suportado; não prova ausência de omissões no PDF.

**Aprendizado dos testes reais:** a LLM acertou os vencedores, mas primeiro
expandiu VD como “Valor de Defesa”; com a definição fornecida, inventou um
intervalo de páginas. Por isso, cálculo **e apresentação** desse ranking agora
são determinísticos, com referências e zero chamadas à API. Exclusões, top-N,
outros critérios e referências ao histórico seguem para RAG.

**Sequência final conferida no chat:**

- “Quais as criaturas mais fortes por elemento?”: cinco resultados corretos,
  seis fontes, cerca de 0,1 s de recuperação, zero chamadas.
- “E a de Conhecimento, quem é?”: manteve o assunto e identificou a Máscara
  do Desespero; três chamadas, 65,5 s totais, 42 s aguardando cota. A resposta ainda
  foi repetitiva e não citou a ficha junto do VD; não contar isso como fidelidade
  perfeita. Outra execução anterior concluiu em uma chamada/12 s: há variação.
- Recarregamento preservou o histórico; citações abriram a fonte da mensagem;
  imagem original retornou 200/PNG. Nenhum erro no console. Larguras CSS 387 e 1688
  sem transbordamento; captura visual indisponível nesta rodada.

**Verificação:** 323 testes automatizados aprovados. Revisão dividida entre
backend/persistência/segurança, recuperação e interface. Oito chamadas reais
nesta etapa: 22.705 tokens de entrada e 2.846 de saída, incluindo tentativas
intermediárias; nenhuma migração de modelo. Não há estimativa monetária porque
os preços não estão configurados.

**Próximas prioridades:** avaliar continuações inéditas e regras que atravessam
páginas; reduzir citações imprecisas e latência de consultas abertas antes de
ampliar fichas além do NEX 5%. Ferramentas agora são opcionais; síntese antecipada
remove histórico técnico quando necessário, sem apagar fontes. Índices consultam
o modelo de embeddings já preparado localmente. Não ampliar para outros livros
nem adotar infraestrutura nova antes de medir ganhos nos casos existentes.

## Dados legíveis e avaliação de builds — 20/10/2026

**Correção:** o livro usa a fonte `d20-Regular`, na qual `O` desenha um d20.
`pdf_text.py` traduz essa fonte antes da divisão em trechos: `3O` → `3d20`,
`OO` → `2d20`. Letras comuns e dados já textuais (`d4`, `d6`, `d8`, `d10`, `d12`)
ficam intactos. A legenda foi conferida no PDF 19/impressa 11. Ícones com sinal
recebem a anotação editorial `[na quantidade de dados]`, porque `−OO` retira
dois dados da quantidade rolada; não subtrai o resultado de uma rolagem de 2d20.
A forma `−2O` das fichas de criaturas é distinta e não recebe essa anotação.

**Aplicação local:** backup SQLite em `data/backups/before-dice-20261021T013215Z.sqlite3`;
93 páginas corrigidas, incluindo 49 com a anotação de quantidade. Índice final:
1.041 trechos e 2.553 vetores. `refresh-text` atualiza páginas/chunks/FTS e invalida
vetores em transação; `index-vectors` os reconstrói. Fichas, campanhas e conversas
foram comparadas ao backup e preservadas. Respostas antigas continuam como
snapshots. Imagens das fontes agora usam página+hash, sem depender do ID do chunk.

**Método:** sete perguntas novas, com gabarito independente do PDF em
`evaluation/build-mechanics-ordem-1.1.json`. A aplicação recebeu somente a pergunta,
pelo endpoint normal `/api/ask`, com GPT-OSS-120B/medium e configurações habituais.
Não recebeu gabarito nem páginas selecionadas manualmente. Não foi feita troca de
modelo ou ajuste dos limites. Casos de regras foram conferidos também em imagens.

| Caso | Gabarito resumido e páginas PDF | Resultado observado |
|---|---|---|
| Força 3/Atletismo treinado e atributo zero | Maior de 3d20 +5; 17+5=22. Atributo zero: menor de 2d20. PDF 22/48. | Correto. |
| Agilidade 3, fuzil sem proficiência | 3−2=1d20; +5 do treinamento. PDF 22/48/62. | Errou. Primeiro inventou dados extras para subtrair; após a glosa entendeu que são dados a menos, mas ignorou Agilidade 3 e inventou zero dados/falha automática. |
| Combatente NEX 5% com VIG 2/PRE 1 | Distribuição válida, 22 PV/3 PE/12 SAN. PDF 22/33. | Sem resposta: reposição da cota de tokens não cabia na janela de pesquisa. |
| Ocultista NEX 5% com VIG 1/PRE 3 | 13 PV/7 PE/20 SAN; três rituais de 1º círculo; 2º só no NEX 25% pela classe. PDF 40/41. | Acertou os rituais, mas usou bônus de outra situação como se fossem iniciais e não informou os valores corretos. |
| Críticos de acha/gadanho, Força 3 | Normal 1d12+3/2d4+3; crítico 3d12+3/8d4+3. PDF 62/65. | Sem resposta: reposição da cota de tokens não cabia na janela de pesquisa. |
| Golpe Pesado e Proteção Pesada | Primeiro poder no NEX 15%; Proteção Pesada exige 30%; Golpe Pesado não lista requisito próprio. PDF 32/33. | Acertou os NEX, mas atribuiu a Golpe Pesado requisitos de outro poder. |
| Sniper NEX 35% | Respeitar classe/trilha, perícia Pontaria e Agilidade; patente depende de prestígio. PDF 36–38/59–60. | Misturou regras de combatente/especialista, inventou “Destreza” e perícia “Armas de Fogo”, aplicou 2+INT no lugar de 5+INT do especialista. Build inválida. |

**Leitura do resultado:** um caso adequado, quatro com erros materiais e dois
inconclusivos por limite temporário de tokens. “answered” e citações numeradas
não comprovam correção factual. A glosa resolveu a representação do símbolo,
mas não tornou confiável a interpretação das regras. A amostra é pequena e não
é uma comparação controlada entre modelos; há falhas de recuperação, de escopo
na interpretação e de orçamento de pesquisa.

**Verificação e uso:** 337 testes automatizados passaram (25,79 s). A busca exibiu
dados legíveis na interface; fonte atualizada e imagem original foram conferidas
por HTTP (200/PNG). O módulo Python de fichas produziu corretamente ambos os
valores base acima. Houve 18 tentativas nesta rodada, sendo 17 chamadas com uso
retornado e uma falha de conexão do ambiente de execução, resolvida ao reiniciar
o servidor com acesso de rede. Uso informado: 52.692 tokens de entrada e 8.423 de
saída. Nenhuma estimativa monetária: preços não configurados.

**Próximo trabalho útil:** conectar o chat à validação determinística de fichas
já existente; recuperar poderes com seu título, classe e pré-requisitos juntos;
reservar cota para a resposta final antes de novas buscas. Reavaliar estes casos
antes de ampliar escopo ou trocar de modelo. A correção desta rodada ficou na
extração/manutenção/fontes; não houve reescrita dos prompts para decorar perguntas.

Reprodução: `python evaluation/run_build_mechanics.py --live` com servidor na
porta 8765; sem `--live`, só lista os casos. Respostas/fontes completas ficam
somente em `data/evaluations/`, ignorado pelo Git; imagens de inspeção em `tmp/`
também são ignoradas. As pastas desta rodada terminam em `013523955659Z`,
`014521461416Z` (falha de conexão) e `021726956127Z`, todas de 21/10 UTC,
correspondente à noite de 20/10 em São Paulo.
