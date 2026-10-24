# Revisão técnica — 21/10/2026

## Conclusão

A arquitetura é proporcional ao uso local com Ordem Paranormal 1.1. Não apareceu
evidência que justifique trocar banco, frameworks, embeddings, reranker ou modelo.
Corrigimos problemas reproduzidos. **O chat ainda não deve certificar builds:**
citações e ferramentas disponíveis não garantem interpretação correta das regras.
A ficha estruturada calcula e valida o recorte NEX 5% diretamente em Python.

## Arquitetura revisada

| Camada | Tecnologia e avaliação |
|---|---|
| PDF | PyMuPDF, texto por página, numeração e seções; conversão dos ícones de dados preservada. Quadros podem perder relações espaciais. |
| Persistência | SQLite: texto canônico, fichas, campanhas, conversas, fontes por mensagem e uso. Consultas parametrizadas e transações revisadas. |
| Recuperação | FTS5 + embeddings multilíngues locais FastEmbed, combinados por RRF. Chroma/LangChain guardam vetores vinculados ao texto SQLite. |
| Reranking | Cross-encoder BGE via FastEmbed/ONNX em CPU. Reordena candidatos; não garante cobertura nem correção da resposta. |
| Agente | Pesquisa limitada, ferramentas permitidas, citações estáveis e orçamento de fontes/chamadas/tempo. Operações escolhidas pela LLM ainda podem ser inadequadas. |
| Regras | Python calcula bases de ficha e máximos do catálogo de criaturas. `character_tools.py` conecta a validação parcial ao chat. |
| Geração | Groq `openai/gpt-oss-120b`, raciocínio `medium`, mantidos. SDK isolado; chave no servidor; erros sem corpo bruto ou credenciais. |
| Interface | HTML/CSS/JavaScript, chat persistente, fontes, fichas e campanha. Estética preservada, sem novo framework. |

SQLite + Chroma evitam administrar um servidor de banco para um usuário. pgvector
pode ser reavaliado com múltiplos usuários/livros; migrar agora não resolveria os
erros observados. LangChain já tem função real na camada vetorial. Não foi
necessário reindexar nem alterar `.env` nesta revisão.

## Correções realizadas

- **Prévias:** preservam entradas de lista e favorecem seu título. O recorte de
  Golpe Pesado antes incluía requisitos de Golpe Demolidor sem o título anterior.
- **Reranking:** candidatos além do grupo reordenado podem completar a seleção
  quando a diversificação por página descarta resultados redundantes.
- **Tempo:** outra rodada considera a espera pela cota e reserva tempo para a
  resposta final. A síntese antecipada preserva fontes e limitações; não supera
  cotas nem garante conclusão em toda consulta.
- **Validação no chat:** ferramenta somente leitura exige classe, NEX e cinco
  atributos; reutiliza Python e devolve parâmetros, cálculos, erros e fontes.
  A interface mostra “Conferência parcial em Python”. Não salva nem certifica
  origem, poderes, perícias, rituais, equipamento ou evolução.
- **Quadros de classe:** fontes com quadros reconhecidos recebem as fórmulas base
  de `CLASS_RULES`, separadas da transcrição e vinculadas a evidências canônicas.
  Isso distingue criação de progressão, sem interpretar atributos da pergunta.
- **Gravação:** editor captura o ID antes da validação e impede troca durante
  operações. Falhas preservam campos; salvar campanha preserva o rascunho de
  acontecimento; falha ao atualizar a tela não invalida um evento já salvo.
- **Backend/fontes:** uma configuração por requisição; busca avulsa também
  informa hash do livro para conferir a página original.
- **Acessibilidade/documentação:** seleção com `aria-current`, trechos acessíveis
  pelo teclado, resultados Python com escopo e parâmetros. Documentação alinhada
  às ferramentas opcionais e às fontes presentes desde a primeira chamada.

O schema não prova que os números vieram do usuário. O modelo pode extrair um
atributo incorretamente ou ignorar a ferramenta; por isso parâmetros ficam
visíveis e ausência de `rule_checks` não equivale a validação executada.

## Testes locais

**377 testes Python aprovados (25,24 s), 10 testes JavaScript aprovados e
`pip check` sem incompatibilidades detectadas.** Cobrem limites, API simulada,
persistência, concorrência, conteúdo hostil, fontes e cálculos suportados.

SQLite: `integrity_check = ok`; 1.041 chunks preservados. Comparação com o backup:
1 ficha, 1 campanha, 1 acontecimento, 3 conversas e 12 mensagens intactos.
Somente os registros de uso foram acrescentados.

Navegação desktop/console foi conferida na revisão da interface, sem erros.
Responsividade revisada no CSS. A conferência visual final e o ensaio móvel
ficaram limitados pelo bloqueio da ferramenta de navegador; não foram
declarados como testes visuais aprovados. Testes automatizados não medem a
correção geral das respostas reais da LLM.

## Testes reais e diagnóstico

Perguntas de `evaluation/build-mechanics-ordem-1.1.json`, com gabarito independente
nunca enviado ao modelo. Um controle usou páginas selecionadas manualmente;
os demais passaram pelo endpoint normal.

| Experimento | Resultado |
|---|---|
| Fuzil sem proficiência, contexto manual PDF 47/48/62 | Correto: 3−2=1d20, penalidade na quantidade. Uma chamada, 1,97 s. |
| Mesma pergunta, recuperação normal | Incorreto: assumiu um dado base e concluiu zero; não recuperou a relação atributo/dados. Duas chamadas, 44,28 s; 26,83 s aguardando cota. |
| Combatente NEX 5%, antes dos fatos estruturados | Distribuição correta; PV/SAN errados: 6/3 em vez de 22/12. Ignorou a ferramenta. Duas chamadas, 66,07 s. |
| Ocultista NEX 5%, antes dos fatos estruturados | PE/rituais corretos; PV/SAN errados: 3/5 em vez de 13/20. Ignorou a ferramenta. Duas chamadas, 57,78 s. |
| Golpe Pesado / Proteção Pesada | Corrigiu requisitos e marcos 15%/30%, mas inventou outra oportunidade no NEX 40%. Parcial, não aprovação factual. Duas chamadas, 64,46 s. |
| Combatente após fatos estruturados | Fórmulas e fontes corretas chegaram ao contexto. A segunda chamada recebeu HTTP 429. Inconclusivo; ocultista não foi repetido. |

Há falhas de recuperação/composição, ambiguidade da extração e erros de
interpretação/seguimento de instruções. O mesmo modelo acertou o controle com
contexto completo: não cabe atribuir tudo à capacidade do GPT-OSS-120B. Isso
também não prova que ele seja suficiente para qualquer build. Não comparamos
modelos nem níveis de raciocínio nesta revisão.

**O ganho da última alteração na geração real não está confirmado.** Fatos
estruturados e ferramenta passaram nos testes locais. O HTTP 429 não permite
identificar aqui se a cota era diária ou por minuto; encerramos as chamadas.

Uso: **11 tentativas**, 10 com uso retornado e uma HTTP 429; **32.109 tokens de
entrada e 5.748 de saída** conhecidos. Sem estimativa monetária: preços não
configurados. Resultados privados estão em `data/evaluations/`, pastas
`20261021T031752588874Z-final-review-reference`,
`20261021T032203187449Z-build-mechanics` e `20261021T032910890763Z-build-mechanics`.

## Segurança e próximos passos justificados

Servidor em `127.0.0.1`, validação de origem/host, limite de corpo e CSP.
Renderização sem HTML arbitrário; ferramentas limitadas, sem comandos ou escrita
de fichas. PDF/histórico são dados, não autorização para ações. `.env`, banco,
índices, PDF e avaliações privadas permanecem ignorados pelo Git. O envio dos
trechos ao Groq foi explicitamente autorizado nesta bateria. Geração depende
de internet/cota; busca local e fichas não dependem da API.

1. Repetir os dois casos NEX 5% quando a API estiver disponível, antes de declarar
   corrigida a interpretação dos quadros. Usar o formulário para valores validados.
2. Melhorar cobertura conjunta de regra base e modificadores; não aumentar
   indiscriminadamente o número de trechos.
3. Avaliar afirmações sem suporte e condições de classe/progressão além de
   conferir a existência da citação. Depois comparar modelos com o mesmo contexto.

Não ampliar para outros livros ou validação de builds avançadas antes desses
casos. Missões continuam propostas criativas, sem certificação de balanceamento.
O aplicativo local não virou serviço público com autenticação multiusuário.

## Reprodução e referências

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/frontend.test.cjs
.\.venv\Scripts\python.exe -m pip check

# Sem rede: lista os casos; --live usa a API.
.\.venv\Scripts\python.exe evaluation/run_build_mechanics.py

# Prepara o controle sem chamar API; --live executa a geração.
.\.venv\Scripts\python.exe evaluation/compare_contexts.py --dataset evaluation/build-mechanics-ordem-1.1.json --manifest evaluation/reference-build-mechanics.json --context reference --cases dice-nonproficient-penalty --label reference-check
```

Node é necessário somente para esses testes da interface. O manifesto de
referência contém páginas/hashes, não texto privado, e só serve à avaliação.

Método conferido nas documentações de
[recuperação e reranking](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html),
[avaliação RAG](https://docs.langchain.com/langsmith/evaluate-rag-tutorial) e
[raciocínio na API Groq](https://console.groq.com/docs/reasoning).
Essas referências sustentam o método; os resultados acima vêm dos testes locais.
