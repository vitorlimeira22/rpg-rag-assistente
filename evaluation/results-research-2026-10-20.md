# Avaliação da pesquisa no livro — 20/10/2026

## Pergunta e método

Caso de calibração: **“qual o ritual mais poderoso ?”**, no livro privado de Ordem Paranormal RPG 1.1.

A pergunta já orientou mudanças. Ela não é um teste cego nem representa todas as consultas possíveis. Não foi criada uma resposta pronta, uma lista de rituais ou uma seleção de páginas específica para essa pergunta no código de produção.

Foram separados dois problemas:

1. **Recuperação:** os primeiros trechos traziam texto geral sobre rituais e habilidades de ocultista, sem evidência suficiente para comparar efeitos.
2. **Interpretação:** mesmo com evidências selecionadas manualmente para a avaliação, o modelo podia perder condições ou tirar conclusões incorretas.

O [conjunto de comparações](comparisons-ordem-paranormal-1.1.json) contém esse caso de calibração e duas perguntas adicionais ainda não executadas com a API. Ele usa revisão manual por afirmação e fontes, sem alterar o gabarito da avaliação de recuperação anterior.

## Modelo com evidências selecionadas manualmente

Quatro chamadas receberam a mesma pergunta e sete evidências selecionadas para diagnóstico: classificação de círculos, três descrições de rituais, uma continuação e regras de acesso/custo relevantes. A seleção manual pertence ao experimento, não ao fluxo de produção.

As quatro execuções usaram a versão de instruções `2026-10-20-v5`, com pesquisa por ferramentas desativada. Houve **uma execução por configuração**. O teste não controla a variação estocástica nem permite estabelecer um ranking geral de modelos.

| Modelo / raciocínio | Entrada / saída | Tempo da API | Observação factual |
|---|---:|---:|---|
| GPT-OSS 120B / low | 2.236 / 656 tokens | 2,37 s | Evitou a comparação e atribuiu eficácia equivalente a opções do mesmo círculo, sem sustentação. |
| GPT-OSS 120B / medium | 2.236 / 1.522 tokens | 3,91 s | Comparou opções, mas confundiu chegar a zero PV e ficar morrendo com morte instantânea; também perdeu condições de efeitos. |
| GPT-OSS 120B / high | 2.236 / 1.973 tokens | 4,80 s | Aumentar o esforço não eliminou os erros de condições e consequências. |
| Qwen 3.8 27B / medium | 2.292 / 1.550 tokens | 4,76 s | Preservou a condição morrendo em uma passagem, mas calculou incorretamente um máximo de dano e perdeu outras condições. |

**Decisão provisória:** manter `openai/gpt-oss-120b` e configurar `GROQ_REASONING_EFFORT=medium` no ambiente local e no exemplo. A comparação mais útil observada não equivale a fidelidade comprovada. Não há evidência suficiente para recomendar uma migração para o Qwen a partir deste único caso.

A versão seguinte das instruções passou a exigir maior precisão em condições, alcance/área, dano condicional e fórmulas. Isso é uma orientação ao modelo, não um validador determinístico desses efeitos.

## Pesquisa pelo aplicativo

### Pesquisa opcional, versão v6

O modelo usou uma busca e uma leitura adicional. A terceira chamada recebeu **429** antes da síntese. Foram preservadas oito fontes e os registros das três tentativas.

As duas primeiras chamadas retornaram 2.115 + 3.418 tokens de entrada e 173 + 88 de saída. O uso da tentativa que falhou é desconhecido; o total não foi apresentado como zero. O tempo total exibido foi de aproximadamente 15,7 segundos.

O erro 429 indica limite do provedor. A sequência e o volume são compatíveis com pressão sobre a cota por minuto, mas o erro redigido para a interface não identifica sozinho qual cota foi atingida.

### Pesquisa opcional, versão v7

Com novas orientações e controle de espera preparado, uma repetição respondeu sem usar ferramentas: uma chamada, 2.216 tokens de entrada, 389 de saída e aproximadamente 12,5 segundos no servidor.

**Resultado reprovado:** a resposta voltou a generalizar um benefício de uma habilidade como se fosse característica dos rituais, interpretou a sigla PE incorretamente e não fez a comparação solicitada. Disponibilizar ferramentas com `tool_choice=auto` não foi suficiente para garantir pesquisa.

### Mudança para pesquisa explícita, versão v8

A primeira chamada do modo de pesquisa passou a solicitar obrigatoriamente uma ferramenta, quando há orçamento para pesquisar e responder. O modelo ainda escolhe a busca ou página; o backend valida o pedido. As rodadas seguintes podem responder ou pesquisar, e a última só sintetiza.

O custo esperado desse modo passa a ser de duas a três chamadas bem-sucedidas com a configuração padrão. O fluxo de uma chamada continua disponível com `CONSULT_TOOLS_ENABLED=false`; `CONSULT_MAX_MODEL_CALLS=1` também impede rodadas de ferramentas.

O teste v8 realizou uma busca e encerrou na segunda chamada. O controle de espera funcionou: aproximadamente 15,8 segundos aguardando a reposição, sem 429, e 31,8 segundos no servidor. Consumo: 5.648 tokens de entrada e 448 de saída.

**Resultado factual reprovado:** voltou a responder em termos de círculo, sem comparar opções. Também usou outro formato de citação numérica, sinalizado como ausência pelo verificador da época. Posteriormente, `【7】` e `【7†】` passaram a ser normalizados para `[7]`; isso corrige a apresentação, sem validar o conteúdo.

### Planejamento separado, versão v9

Para reduzir a influência de candidatos iniciais irrelevantes, a primeira etapa passou a planejar buscas a partir da pergunta. Os trechos iniciais são apresentados nas etapas seguintes.

Foram três chamadas, duas buscas e nenhuma leitura de descrição completa: 8.674 tokens de entrada, 1.013 de saída, 49,7 segundos no total, dos quais 27,2 aguardando a cota. **Resultado ainda reprovado:** respondeu novamente com uma equivalência injustificada entre opções do mesmo círculo. A busca inicial proposta pelo modelo também não seguia uma descoberta de candidatos no livro.

### Guia de navegação, versão v10

Um mapa de títulos e intervalos de páginas relacionados à pergunta passou a acompanhar o planejamento. O algoritmo usa metadados existentes, sem referências fixas. O [diagnóstico local](navigation-discovery-2026-10-20.md) documenta a dificuldade de encontrar descrições por consultas amplas e a utilidade do catálogo para descobrir nomes.

No teste real, o modelo fez duas buscas genéricas antes de ler o catálogo; isso consumiu três etapas de ferramentas e deixou apenas a síntese. Foram quatro chamadas: 14.915 tokens de entrada, 1.069 de saída e 83,8 segundos no servidor, incluindo 60,6 segundos de espera pela cota, distribuídos entre etapas.

**Resultado ainda insuficiente:** descobriu nomes reais, mas não leu as descrições e não realizou a comparação pedida. A existência de um mapa não garantiu que o modelo escolhesse a sequência mais eficiente.

### Recuperação de catálogo, versão v11

A recuperação passou a abrir automaticamente até duas páginas de um catálogo curto identificado nos títulos relacionados à pergunta. Essa leitura conta como uma das quatro operações permitidas. Quando fornece fontes, substitui as menções genéricas iniciais; quando não encontra catálogo pertinente, mantém o fluxo anterior.

As buscas por ferramentas passaram a retornar até três prévias de 400 caracteres, marcadas quando cortadas. Essa separação reserva espaço para as páginas completas: buscar serve para localizar e ler serve para conferir. O teto de evidências continua sendo 16 mil caracteres e o teto de chamadas ao modelo passou a quatro.

O comportamento depende da estrutura de títulos extraída do PDF. Um catálogo sem esses marcadores, com mais de duas páginas ou sem relação lexical com a pergunta não é carregado automaticamente. Não se assume que os resumos de um catálogo comprovem custos e condições omitidos.

No teste v11, o modelo passou a buscar **Inexistir** e **Fim Inevitável**, nomes encontrados no catálogo. Isso demonstra descoberta de candidatos nesse caso. Entretanto, repetiu a segunda busca; o cache evitou executá-la de novo, mas a rodada e o pedido continuaram consumindo o orçamento.

Foram três chamadas: 11.063 tokens de entrada, 553 de saída e 67,7 segundos no servidor, incluindo 46,1 segundos de espera entre chamadas. A aplicação interrompeu antes da síntese porque exigia reservar o timeout completo após a próxima espera. As fontes foram preservadas.

**Resultado operacional:** `research_limit`. **Qualidade da resposta final:** não avaliada, pois não houve síntese. A comparação solicitada continua sem aprovação de ponta a ponta.

Após esse teste, o controle de tempo foi corrigido: uma chamada pode usar um timeout menor, correspondente ao tempo restante, desde que a espera caiba com uma margem mínima de cinco segundos. Um teste com relógio simulado confirmou uma síntese com 9,75 segundos disponíveis. **Essa correção final não foi repetida com a API real nesta rodada.** Ela não resolve por si só a escolha ruim ou repetida de buscas.

## Controle de uso e alcance da verificação

O adaptador lê somente valores numéricos normalizados dos cabeçalhos de tokens do Groq. Entre etapas, a aplicação estima o tamanho do próximo pedido e, quando necessário, aguarda a reposição informada pelo provedor, respeitando seus próprios limites de tempo. Não há repetição automática de chamadas com falha.

Essa estimativa não é a contagem exata do tokenizer nem reserva a cota da organização: outros clientes podem consumi-la. O controle diário local continua separado e inclui cada tentativa real, inclusive falhas. Ao chegar a 19 das 20 chamadas, foi solicitada e recebida autorização do usuário para elevar temporariamente o limite a 30 e restaurar 20 ao concluir. Isso não muda o plano ou as cotas da conta Groq.

**Suíte final: 235 testes passaram em 12,44 segundos.** Inclui navegação derivada de metadados, seleção de catálogos, prévias limitadas, integração das ferramentas, reserva de chamadas, espera por tokens e normalização de referências. A suíte testa contratos e falhas simuladas; não mede a confiabilidade factual do modelo.

O dia terminou com **29 chamadas registradas**, dentro das 30 autorizadas temporariamente. O limite normal de 20 foi restaurado. Como as tentativas já consumidas permanecem registradas, novas gerações ficam bloqueadas pela cota local até a renovação diária em UTC ou nova alteração autorizada. A busca local de trechos continua disponível.

## Conclusão e trabalho ainda necessário

- A interface, as ferramentas restritas, os limites e a navegação foram implementados e testados.
- O modelo contribuiu para falhas de interpretação e escolhas ruins de pesquisa, inclusive com fontes corretas.
- A recuperação ganhou um caminho para descobrir opções pelo catálogo, mas isso ainda não produziu uma comparação final aprovada no teste.
- O erro de geração não deve ser escondido por uma afirmação de que o RAG foi “resolvido”.
- A próxima avaliação deve repetir a versão final e executar os dois casos novos, mantendo a separação entre recuperação, fidelidade e falha operacional. Só depois é possível decidir se outro modelo melhora o fluxo inteiro.

## Interface

As cores e famílias tipográficas foram adaptadas com referência ao [site oficial de Ordem Paranormal](https://ordemparanormal.com.br/): superfícies escuras, destaque âmbar e títulos condensados. Foram usadas fontes locais com alternativas; não foram acrescentadas imagens, downloads de fontes ou dependências externas.

O layout existente foi preservado. A interface foi conferida no navegador a 1.366 px e 390 px, com largura do documento igual à área disponível. Capturas sem conteúdo privado do livro: [desktop](../docs/images/interface-ordem-desktop.png) e [celular](../docs/images/interface-ordem-mobile.png).

## Referências técnicas

- [Groq: ferramentas executadas pela aplicação](https://console.groq.com/docs/tool-use/local-tool-calling).
- [Groq: limites e cabeçalhos de uso](https://console.groq.com/docs/rate-limits).
- [Groq: parâmetros de raciocínio](https://console.groq.com/docs/reasoning).

Textos extensos do livro, respostas brutas de diagnóstico e dados privados permanecem em `data/`, ignorado pelo Git.
