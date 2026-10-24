# Descoberta de candidatos por navegação

Inspeção local em 20/10/2026, com Ordem Paranormal RPG 1.1. Nenhuma chamada
à API de geração e nenhuma reconstrução do índice. Este é um diagnóstico
exploratório de calibração; não mede a qualidade de respostas finais.

## Recuperação antes do guia

Consultas executadas com busca híbrida, 30 candidatos por método e reranker
local sobre os 10 primeiros candidatos combinados:

| Consulta | Descrições no top 10 antes do reranker | Descrições no top 5 final |
|---|---:|---:|
| rituais de quarto circulo efeitos dano custo | 1 | 1 |
| rituais de dano em area | 4 | 3 |
| rituais de controle de inimigos | 2 | 2 |
| rituais custos PE | 1 | 1 |
| rituais de defesa e protecao | 1 | 1 |

“Descrição” significa que o trecho pertence à seção de descrições no livro;
não significa que o texto esteja completo, correto para o objetivo ou seja a
melhor opção. Vários resultados começam no meio de uma descrição ou de um
aprimoramento, sem identificar o ritual. As consultas aquecidas levaram de
3,88 a 4,65 segundos; a primeira, incluindo carregamento, 10,38 segundos.

## Causas observadas

- Somente 13 dos 89 chunks de descrições contêm a forma plural “rituais”.
  Na introdução do assunto, essa forma aparece em 18 dos 21 chunks.
- O FTS consulta apenas o corpo do trecho. Capítulo e seção existem no
  SQLite e no Chroma, mas não compõem o texto pesquisado, os embeddings
  ou o texto do reranker.
- A fusão prioriza trechos encontrados pelos dois métodos. Menções gerais
  e fichas de ameaças ocuparam vagas anteriores ao corte de 10 candidatos.
  O reranker não pode resgatar um candidato que ficou fora desse corte.
- Incluir cabeçalhos em um FTS temporário melhorou parte da descoberta,
  mas a consulta sobre defesa/proteção ainda não encontrou descrições no
  top 10. Acrescentar títulos isoladamente não demonstrou resolver o caso.

## Experimento de navegação e descoberta

Uma consulta aos títulos persistidos encontrou os intervalos físicos:
Rituais, 125–129; Lista de Rituais, 130–131; Descrição dos Rituais, 132–151.
Esses intervalos vêm dos metadados, sem referências fixadas no algoritmo.

A lista do livro foi lida para descobrir nomes; depois, seus cinco primeiros
nomes após o último cabeçalho de círculo foram usados em buscas literais.
Não foram escolhidos pelo resultado da busca:

| Nome descoberto no catálogo | Página PDF da descrição | Posição entre os três resultados textuais |
|---|---:|---:|
| Controle Mental | 136 | 1 |
| Inexistir | 142 | 3 |
| Possessão | 146 | 1 |
| Alterar Destino | 132 | 1 |
| Deflagração de Energia | 137 | 1 |

O próprio catálogo foi excluído dessas buscas para verificar a chegada à
descrição. Resultado: **5/5 descrições encontradas no top 3 desse experimento**.
Isso não é acurácia geral, comparação de poder nem avaliação cega. Um nome
ambíguo também apareceu em texto narrativo, justificando ler a página canônica.

## Adaptação proposta

O helper `book_navigation` retorna um mapa limitado de títulos e intervalos
relacionados à consulta. Ele não transforma metadados em evidência. O modelo
pode ler uma lista, descobrir nomes, procurá-los e abrir descrições completas
antes de responder. A sequência ainda precisa de avaliação real da aplicação.

O matching normaliza acentos e admite flexões próximas por prefixo. Não
resolve sinônimos: uma consulta sem correspondência retorna mapa vazio.
Os testes verificam limites, intervalos não contíguos, isolamento do livro e
uso exclusivo de metadados em consultas de leitura.

Na conferência do helper com o banco real, a pergunta de calibração retornou
as três seções do assunto em 521 caracteres. Consultas sobre armas e proteções
retornaram suas seções; uma consulta sobre astronaves retornou vazio. As cinco
execuções levaram de 1,47 a 5,10 ms. Os 14 testes do helper e os 24 testes das
ferramentas de leitura/busca passaram juntos. Não houve teste de geração nesta
etapa; o mapa pode orientar a pesquisa, mas não garante que o modelo a conclua.
