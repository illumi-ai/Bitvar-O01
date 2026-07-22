# Especificação de Requisitos · v1.0 — Vertical Academia / Musculação (Pivô)

> **Produto**: motor de análise postural por vídeo com IA (Illumi) · **Nova vertical**: análise de execução de
> exercícios de academia · **MVP**: agachamento.
> **Fonte**: conversa WhatsApp Caio Bitvar ↔ Mateus | illumi (10/06–15/07/2026). Rastreabilidade em
> [`ANEXO-transcricoes-fonte.md`](./ANEXO-transcricoes-fonte.md).
> **Status da decisão**: pivô aprovado pelo cliente (Caio) com prioridade sobre o beach tennis (`00000523`, 15/07/2026).

**Selos de origem** (em cada requisito/regra):
- `Explícito [arquivo · data]` — dito literalmente numa mensagem/áudio da conversa.
- `Derivado` — consequência necessária (boa prática, coerência com o motor existente, lição do beach tennis).

Prioridade (MoSCoW): `MUST` (essencial à 1ª entrega/POC) · `SHOULD` (importante, negociável) · `COULD` (desejável/futuro).

---

## 01 · Visão Geral

**O que é.** Uma **vertical nova** do produto de análise postural da Illumi, dedicada a **avaliar a execução de
exercícios de musculação a partir de um vídeo** enviado pelo usuário, e devolver um **relatório do praticante**
(o que está sendo feito + o que precisa melhorar). Reutiliza o **motor** já existente — ingestão de vídeo,
segmentação, compreensão multimodal frame a frame, análise guiada por metodologia e trackeamento de evolução —
trocando o **domínio de análise** de "gesto esportivo" para "**biomecânica de exercício de academia**"
(`00000012`, `00000013`, `00000518`).

**Por que existe (estratégia).** Oportunidade comercial concreta: um contato do Caio é **amigo pessoal do Cariani**
(rede de academias de grande porte); a meta é **incluir a ferramenta na rede e vender nacionalmente**
(`00000519`, `00000523`). Tecnicamente, é o caminho de **menor esforço e maior probabilidade de sucesso**:
movimentos lentos e cíclicos, uma pessoa em quadro, sem rastreio de bola — "mais viável até do que o beach tennis"
(`00000518`). Serve tanto como **produto** quanto como **peça de demonstração** para fechar o canal.

**Proposta de valor por ator.**
- **Praticante/aluno**: feedback objetivo da execução sem depender de um personal ao lado o tempo todo.
- **Personal/treinador**: relatório padronizado por aluno; escala o acompanhamento técnico.
- **Rede de academias (Cariani)**: diferencial tecnológico embarcável na rede; produto vendável nacionalmente.
- **Illumi/Caio**: nova vertical com go-to-market rápido reaproveitando ativo existente.

**Escala à substância.** Assunto de porte médio (pedido claro, mas com metodologia técnica ainda a definir):
seções 06+07 e 10+11 são enxutas; o núcleo (03 escopo, 05 requisitos, 09 cenários, 10 questões) é completo.

---

## 02 · Atores & Stakeholders

| Ator | Papel | Origem |
|---|---|---|
| **Caio Bitvar** | Dono da visão de produto/negócio; conduz a relação comercial e define prioridade | `00000517`, `00000519`, `00000523` — Explícito |
| **Mateus \| illumi** | Desenvolve o motor e a vertical; avalia viabilidade técnica | `00000518`, `00000520` — Explícito |
| **Contato do Caio ("o rapaz"/interessado)** | Ponte comercial; amigo pessoal do Cariani | `00000519`, `00000523` — Explícito |
| **Rede Cariani** | Canal de distribuição-alvo (rede de academias) | `00000519`, `00000523` — Explícito |
| **Praticante / aluno** | Usuário final avaliado; sujeito do relatório | `00000523` ("relatório do atleta") — Explícito |
| **Personal / treinador** | Usuário operador; sobe o vídeo e interpreta o relatório | Derivado (herda o papel de "treinador/pai/jogador" do beach tennis — `00000436`) |

---

## 03 · Escopo

### Dentro do escopo (1ª entrega / POC)
- Análise de **um exercício: o agachamento**, a partir de **vídeo enviado** — `Explícito [00000517/00000523]` · **MUST**
- Geração de **relatório do praticante**: o que está fazendo + o que melhorar — `Explícito [00000523]` · **MUST**
- **POC isolada** (fora da plataforma principal), como foi feito no beach tennis, para **demonstrar ao cliente** —
  `Explícito [00000513-chat/00000523]` / `Derivado` · **MUST**
- Página web simples de **upload de vídeo → relatório** — `Derivado` (padrão do motor, `00000013`) · **MUST**

### No escopo próximo (após validar o agachamento)
- **Bíceps** (rosca) como 2º exercício — `Explícito [00000519]` · **SHOULD**
- **Trackeamento de evolução** entre vídeos do mesmo aluno — `Derivado` (`00000012`) · **SHOULD**

### Fora do escopo desta fase
- **Todos os exercícios de academia** (biblioteca completa) — visão declarada, mas **não** é a 1ª entrega —
  `Explícito [00000523]` (ambição) → adiado · **COULD**
- **Integração à plataforma/rede** (login, multiusuário, billing da rede) — `Derivado` · **COULD**
- Retomada da **calibração do beach tennis** — explicitamente **posterior** ("depois a gente regula o bit",
  `00000523`) · fora desta spec
- Contagem de repetições / carga / prescrição de treino — não mencionado · fora

**Público-alvo / momento.** Demo para o contato da rede Cariani, com janela comercial curta; prioridade sobre o
beach tennis por decisão do Caio (15/07/2026).

---

## 04 · Jornada & Fluxos

**Fluxo ponta a ponta (POC):**

1. **Captura** — o praticante/treinador grava o agachamento seguindo orientações de enquadramento (ângulo,
   distância, corpo inteiro visível).
2. **Upload** — envia o vídeo na página da POC.
3. **Segmentação** — o motor recorta o vídeo em repetições/fases do movimento.
4. **Análise** — a IA avalia cada repetição contra a **metodologia do agachamento** (checklist biomecânico).
5. **Relatório** — gera diagnóstico: pontos corretos + pontos a corrigir, em linguagem acessível.
6. **(Futuro) Evolução** — novo vídeo do mesmo aluno é comparado ao anterior para medir progresso.

**Recorte de entrada (lição do beach tennis, `00000436`).** Orientar o usuário a enviar **poucas repetições bem
enquadradas** em vez de um treino inteiro, para manter a análise clara e objetiva — `Derivado`.

---

## 05 · Requisitos Funcionais

### A. Ingestão e captura de vídeo
| ID | Requisito | Prioridade | Origem |
|---|---|---|---|
| **RF-001** | Permitir **upload de um vídeo** de agachamento em página web simples da POC. *Critério: o usuário consegue subir o arquivo e receber um resultado sem instalação.* | MUST | Derivado (`00000013`) |
| **RF-002** | Exibir **orientações de captura** (ângulo de câmera, distância, corpo inteiro visível, 1 pessoa em quadro) antes/junto ao upload. | SHOULD | Derivado (lição `00000436`, `00000516`) |
| **RF-003** | Aceitar **vídeo com poucas repetições** do movimento (recorte curto) e orientar o usuário a não enviar o treino inteiro. | SHOULD | Derivado (`00000436`) |

### B. Análise do movimento
| ID | Requisito | Prioridade | Origem |
|---|---|---|---|
| **RF-004** | **Segmentar** o vídeo em partes/repetições para análise criteriosa de cada uma. | MUST | Explícito (`00000012`) |
| **RF-005** | Analisar o vídeo **frame a frame** (visão multimodal) para avaliar a postura ao longo do movimento. | MUST | Explícito (`00000012`) |
| **RF-006** | Avaliar a execução do **agachamento** contra uma **metodologia/checklist biomecânico** parametrizável via prompt. *Critério: cada item do checklist recebe um veredito (adequado / a corrigir).* | MUST | Explícito (`00000517`, `00000519`) |
| **RF-007** | Identificar e descrever **desvios de execução** (ex.: profundidade insuficiente, joelho em valgo, coluna, apoio dos pés). *A lista exata depende de Q-02.* | MUST | Explícito (`00000523` "o que tem que melhorar") + Derivado |
| **RF-008** | **Isolar a pessoa em quadro** para atribuir a análise ao praticante correto. *No agachamento é trivial (1 pessoa), mas o requisito herda a criticidade vista no beach tennis.* | SHOULD | Derivado (contraste `00000516`, `00000518`) |

### C. Relatório
| ID | Requisito | Prioridade | Origem |
|---|---|---|---|
| **RF-009** | Gerar **relatório do praticante** contendo (a) o que está sendo feito e (b) o que precisa melhorar. *Critério: relatório legível por leigo, pronto para enviar ao cliente.* | MUST | Explícito (`00000523`) |
| **RF-010** | Escrever o relatório em **linguagem acessível** (não só técnica), adequado para mostrar ao cliente/aluno. | SHOULD | Explícito (`00000517` "mostrar pro cliente") + Derivado |
| **RF-011** | Permitir **salvar/exportar** o relatório para divulgação/compartilhamento. | COULD | Derivado (paralelo ao beach tennis: "salvar o relatório", `_chat.txt` L168) |

### D. Extensão a outros exercícios (pós-MVP)
| ID | Requisito | Prioridade | Origem |
|---|---|---|---|
| **RF-012** | Estruturar a metodologia por exercício de forma que **novos exercícios** (ex.: bíceps/rosca) sejam adicionados como **novos "prompts/perfis"** sem reescrever o motor. | SHOULD | Explícito (`00000519`, `00000523`) |
| **RF-013** | **Trackear evolução** do mesmo praticante comparando vídeos ao longo do tempo. | SHOULD | Explícito (`00000012` "padrão de evolução") |

---

## 06 · Requisitos Não Funcionais

| ID | Categoria | Requisito | Origem |
|---|---|---|---|
| **RNF-001** | Usabilidade | Fluxo de upload→relatório utilizável por leigo (aluno/personal), sem treinamento. | Derivado |
| **RNF-002** | Precisão | Buscar nível de acerto que torne o produto **viável** (referência de negócio do cliente: ~**95%** de confiança) na avaliação da execução. | Explícito (`00000422`, `_chat.txt` L249-250) |
| **RNF-003** | Desempenho | Resultado da análise em tempo tolerável para demo (padrão do motor: "esperar alguns minutos"). Modelos mais capazes podem aumentar a latência — aceitável na POC. | Derivado (`_chat.txt` L136, L302) |
| **RNF-004** | Reuso/Arquitetura | A vertical academia é uma **variante isolada** ("duplicar o projeto") que reaproveita o motor; não deve degradar o produto de beach tennis. | Explícito (`00000518`) |
| **RNF-005** | Portabilidade | POC acessível por web (link), como no beach tennis, sem depender da plataforma principal. | Derivado (`00000013`, `_chat.txt` L135) |
| **RNF-006** | Confiabilidade | Falha na análise de um vídeo não deve derrubar o fluxo; retornar mensagem clara. | Derivado |

---

## 07 · Regras de Negócio

- **RN-01** — **Prioridade academia sobre beach tennis**: a entrega do prompt/POC de agachamento vem **antes** de
  qualquer nova mexida no beach tennis. `Explícito [00000523]`
- **RN-02** — **MVP = 1 exercício**: a 1ª entrega cobre **somente o agachamento**; demais exercícios só após validação.
  `Derivado` (de `00000517` "só um prompt básico… agachamento").
- **RN-03** — **Relatório sempre construtivo**: além de apontar erros, reconhecer o que já está correto antes de
  ajustar (padrão pedagógico defendido pelo cliente). `Explícito [00000436]`
- **RN-04** — **Metodologia embutida**: a avaliação segue critérios de uma metodologia definida (não opinião livre
  da IA); o exercício só entra em produção com sua metodologia especificada. `Explícito [00000012]` + `Derivado`
- **RN-05** — **POC isolada para venda**: a peça entregável ao cliente é uma demonstração fora da plataforma, para
  destravar a negociação com a rede. `Derivado` (`00000013`, `00000523`)

---

## 08 · Entidades & Dados (modelo conceitual)

> Modelo conceitual — nomes físicos/tabelas ficam para a engenharia no repositório.

- **Exercicio** *(novo)* — `id (PK)`, `nome` (ex.: "Agachamento", "Rosca bíceps"), `descricao`.
- **MetodologiaAvaliacao** *(novo)* — `id (PK)`, `exercicio_id (FK→Exercicio)`, `versao`, `criterios[]`
  (checklist biomecânico: item, o que observar, faixa adequada). *Conteúdo depende de Q-02.*
- **Praticante** *(novo)* — `id (PK)`, `nome`, `dados_opcionais` (altura/peso, se fornecidos).
- **AnaliseExecucao** *(novo)* — `id (PK)`, `praticante_id (FK)`, `exercicio_id (FK)`, `metodologia_id (FK)`,
  `video_ref`, `data`, `segmentos[]`, `veredito_por_criterio[]`, `status`.
- **Relatorio** *(novo)* — `id (PK)`, `analise_id (FK→AnaliseExecucao)`, `pontos_corretos[]`,
  `pontos_a_melhorar[]`, `texto_acessivel`, `exportavel`.
- **VideoEntrada** *(novo)* — `id (PK)`, `analise_id (FK)`, `arquivo`, `enquadramento_ok`, `duracao`, `n_repeticoes`.

**Relações (cardinalidade):**
`Exercicio 1—N MetodologiaAvaliacao` · `Praticante 1—N AnaliseExecucao` · `Exercicio 1—N AnaliseExecucao` ·
`AnaliseExecucao 1—1 VideoEntrada` · `AnaliseExecucao 1—1 Relatorio`.

*Reaproveitados do motor (existentes ◇):* pipeline de ingestão/segmentação de vídeo e o mecanismo de
"padrão de evolução"/trackeamento (`00000012`).

---

## 09 · Cenários de Aceitação

- **CA-01 — Agachamento correto**
  *Dado* um vídeo de agachamento com boa execução e enquadramento adequado,
  *quando* o usuário sobe o vídeo na POC,
  *então* o relatório confirma os pontos corretos por critério e não inventa erros inexistentes. `MUST`

- **CA-02 — Agachamento com desvio**
  *Dado* um vídeo com desvio claro (ex.: profundidade insuficiente ou joelho em valgo),
  *quando* a análise roda,
  *então* o relatório aponta especificamente o desvio e a correção recomendada, em linguagem acessível. `MUST`

- **CA-03 — Vídeo fora do padrão de captura**
  *Dado* um vídeo mal enquadrado (corte do corpo, ângulo ruim, várias pessoas),
  *quando* o usuário tenta analisar,
  *então* o sistema sinaliza a limitação/pede novo recorte em vez de produzir um laudo não confiável. `SHOULD`

- **CA-04 — Peça de demonstração**
  *Dado* um relatório gerado,
  *quando* o Caio o abre para enviar ao contato da rede,
  *então* o relatório é apresentável a um leigo e transmite valor sem edição adicional. `MUST`

- **CA-05 — Segundo exercício (bíceps)** *(pós-MVP)*
  *Dado* a metodologia do bíceps cadastrada como novo perfil,
  *quando* um vídeo de rosca é enviado,
  *então* o motor produz o relatório usando o mesmo pipeline, sem alteração de código do motor. `SHOULD`

---

## 10 · Questões em Aberto

| ID | Questão | Contexto / origem |
|---|---|---|
| **Q-01** | **Onde estão os vídeos de agachamento** de referência (masc/fem, ângulos)? Não estão neste export (todos os vídeos verificados são beach tennis). | `00000517`, `00000523` — bloqueia a POC |
| **Q-02** | Qual a **metodologia/checklist biomecânico** do agachamento (itens, faixas adequadas, desvios a detectar)? Quem é a fonte especialista (personal/fisio)? | `00000517`/`00000523` pedem "o que melhorar" mas não definem os critérios |
| **Q-03** | **Padrão de captura** para academia: ângulo (lateral/frontal), distância, iluminação, roupa, nº de repetições. | Derivado da lição beach tennis (`00000436`, `00000516`) |
| **Q-04** | O relatório sai **por repetição** ou **consolidado** por série/vídeo? | Não especificado |
| **Q-05** | A POC precisa de **cadastro/identificação do praticante** ou é anônima na demo? | Não especificado; "relatório do atleta" sugere identificação (`00000523`) |
| **Q-06** | **Prazo** da demo para o contato da rede Cariani (janela comercial)? | Urgência implícita em `00000519`/`00000523`; agenda da Illumi apertada (`00000520`) |
| **Q-07** | Lista e ordem dos **próximos exercícios** após agachamento e bíceps. | `00000523` "todos os exercícios" (sem lista) |

---

## 11 · Métricas de Sucesso

- **Viabilidade técnica**: % de acerto na avaliação da execução do agachamento; referência de negócio ~**95%**
  (`00000422`, `_chat.txt` L249-250).
- **Prontidão comercial**: 1 relatório de agachamento apresentável enviado ao contato da rede.
- **Reuso**: nº de exercícios adicionados sem alterar o motor (validação de RF-012).
- **Adoção potencial**: entrada na rede Cariani (nº de academias/alunos) — indicador de negócio de médio prazo.

---

## 12 · Glossário

- **Motor / arquitetura** — pipeline de IA reutilizável: ingestão → segmentação → análise multimodal → relatório →
  evolução, criado para oratória e aplicado a esporte e agora academia (`00000012`, `00000013`).
- **Vertical academia** — variante do produto voltada à análise de exercícios de musculação.
- **POC** — página/demonstração isolada (fora da plataforma) para validar e vender o conceito.
- **Padrão de evolução / trackeamento** — comparação de vídeos do mesmo usuário ao longo do tempo para medir progresso.
- **Beach tennis / "o bit"** — produto atual (esporte); referência de contraste e prioridade posterior.
- **Cariani** — Renato Cariani; contato do interessado dá acesso a rede de academias — canal-alvo de venda nacional.

> **Notas de transcrição (ASR)**: "Beat Tennis" = beach tennis; "bit" = beach tennis; "João/Jão e Agro" e
> "João 3.0" = outro projeto do Mateus (fora de escopo, citado só como motivo de agenda); "Cariani" = Renato Cariani.

---
*Documento v1.0 · fonte: WhatsApp Caio Bitvar ↔ Mateus | illumi (10/06–15/07/2026) · gerado a partir de*
*`_chat.txt` + transcrições em `ANEXO-transcricoes-fonte.md` · vertical: Academia/Musculação (MVP agachamento).*
