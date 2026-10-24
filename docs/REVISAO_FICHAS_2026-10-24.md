# Revisão de fichas — 24/10/2026

## Entrega

Consulta geral do livro, missões e personagens têm fluxos próprios. A área Fichas usa editor central e chat lateral; em telas menores, abas. O SQLite guarda a ficha estruturada e a conversa. O PDF é um formato de exportação, preservando o modelo original e seus campos editáveis, com complemento para textos longos.

O chat propõe alterações em JSON. Python normaliza a notação do livro, valida tipos, completa regras conhecidas e confere o resultado. Revisões e UUIDs protegem edições manuais e evitam repetir envios. Uma proposta que continua inconsistente após uma correção não altera a ficha. Pedidos de criação pendentes conservam escolhas explícitas enquanto não houver edição manual posterior.

## Decisões e correções

- Roteiro de instruções com nove etapas de criação, orientação distinta para revisão/edição e exemplos pequenos de formato. Não contém uma resposta pronta de Combatente NEX 15.
- Recuperação por aspectos e seções, com prioridade ao título de uma opção explicitamente pedida. Corrigido o caso em que menções incidentais favoreciam Executivo/Infiltrador em vez de Investigador/Técnico.
- Atributos, habilidades obrigatórias, quantidade de perícias, alguns efeitos de origem/trilha, patente, carga, munição e parte dos pré-requisitos são tratados pelo perfil Python de Ordem 1.1. Há sugestões revisáveis para escolhas que o modelo não completou.
- Categorias romanas e unidades textuais são normalizadas na fronteira da integração. Itens conhecidos usam os dados conferidos do catálogo. Sugestões fora dele não recebem estatísticas presumidas na criação.
- Descrições reconhecidas vêm de trechos canônicos. O resumo usa os campos efetivamente aplicados; não repete explicações do modelo contraditas pela conferência.
- Groq e GPT-OSS 120B foram mantidos. O pedido de geração foi reduzido a escolhas, sem exigir que o modelo reproduza toda a ficha e as regras.
- Orçamento estimado de tokens da ficha configurável; descrições duplicadas são omitidas do estado enviado. Raciocínio low nesse fluxo, com cálculos e descrições locais. A consulta geral mantém sua configuração própria. Erros de API não são repetidos automaticamente.
- Reranking permanece opcional na consulta geral. O fluxo de fichas consulta seções e não depende dele. A medição exploratória anterior encontrou cerca de 3 s de reranking em CPU, sem melhoria das páginas esperadas nos quatro casos observados; isso não é uma avaliação ampla de relevância.

## Verificação

Testes automatizados cobrem progressão das três classes em NEX 5/10/15, exceções implementadas, preenchimento, categorias, patente, carga, preservação de edições, retomada, conflitos, referências e PDF. A bateria completa foi executada durante a revisão; alterações finais tiveram testes direcionados. Os 14 testes JavaScript passaram. A interface foi conferida em desktop e celular, sem transbordamento horizontal ou erros de console no fluxo observado.

Testes reais repetiram o Combatente NEX 15 e incluíram Especialista NEX 10. Foram encontradas falhas de contexto, tipos, escolhas, equipamentos e limites da API; elas orientaram as correções gerais, sem inserir respostas prontas para as perguntas. Um pedido de Especialista retornou proposta em uma chamada, aproximadamente 2,2 s; esse tempo não comprova correção integral. O perfil foi endurecido depois para retirar sugestões sem estatísticas implementadas. Houve diversas falhas intermediárias de formato/cota, registradas no contador existente; os tokens não foram disponibilizados em todas as tentativas.

O exemplo Combatente NEX 15 ficou salvo como rascunho revisado e foi exportado. Os 332 campos editáveis foram preservados e as três páginas do resultado foram inspecionadas. Setas e operadores comuns recebem notação equivalente; caracteres sem suporte geram erro visível.

## Limites importantes

A conferência automática é parcial: nenhuma ficha recebe certificação completa. O recorte de cálculo é NEX 5/10/15; nem todos os poderes, equipamentos, interações e regras da mesa estão implementados. O catálogo de itens é um subconjunto. Sugestões automáticas são pontos de partida revisáveis, não otimização global de builds. Campos como ataques detalhados e proteção vestida ainda exigem conferência/complemento manual no PDF. A Defesa não presume que uma proteção do inventário esteja vestida.

A cota observada do provedor foi 8.000 tokens/minuto. Janela de contexto grande não elimina essa restrição. Uma correção pode exigir espera; erros preservam a ficha. Não foi feita comparação controlada com outro modelo nesta revisão. Os exemplos e testes não demonstram qualidade uniforme para qualquer pergunta do livro.

## Arquivos principais

- `sheet_chat.py`: instruções, orçamento, integração e correção limitada.
- `character_evidence.py`: evidências por aspectos e metadados.
- `sheet_creation.py`: preenchimento do recorte conhecido na criação.
- `sheet_models.py`: dados e validações mecânicas parciais.
- `sheet_presenter.py`: descrições conferíveis e resumo aplicado.
- `sheets.py`: persistência, histórico e controle de revisão.
- `sheet_pdf.py`: adaptação e verificação do formulário privado.

O livro, o modelo PDF, os dados privados e as credenciais permanecem fora do Git. Não ampliar para outros sistemas antes de avaliar mais casos representativos, incluindo perguntas reservadas que não orientem os ajustes.
