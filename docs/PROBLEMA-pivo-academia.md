# PROBLEMA — Pivô para Análise Postural de Academia / Musculação

## Metadados
- **Conversa**: Caio Bitvar ↔ Mateus | illumi (WhatsApp 1:1)
- **Fonte**: `_chat.txt` + áudios `.opus`/`.m4a` (export WhatsApp)
- **Período analisado**: 10/06/2026 a 15/07/2026 (foco do pivô: 10/07 e 15/07/2026)
- **Mensagens**: 547 linhas de chat; ~120 mídias. Áudios processados para este doc: 18 (fundamento + pivô)
- **Severidade / prioridade de negócio**: **P1 — Alto** (oportunidade comercial quente com janela curta; pedido explícito para priorizar sobre o produto atual)
- **Rastreabilidade**: ver [`ANEXO-transcricoes-fonte.md`](./ANEXO-transcricoes-fonte.md)

---

## 1. Resumo executivo

A Illumi (Mateus) desenvolveu um **motor de análise postural por vídeo com IA** — originalmente criado para
**oratória** (feedback de postura corporal durante palestras, segundo a metodologia de uma expert) e depois
aplicado ao **beach tennis** (análise técnica de atletas: saque, ataque, recepção, movimentação). O produto de
beach tennis está em fase de **calibração** (meta declarada: ~95% de acerto na identificação do atleta e do gesto).

Nas mensagens mais recentes (**10/07 e 15/07/2026**), Caio pede um **pivô/expansão**: aplicar o **mesmo motor** à
**análise de exercícios de academia/musculação**, começando por **um exercício isolado — o agachamento** — para
gerar um **relatório de execução do praticante** (o que ele faz e o que precisa melhorar). O gatilho é uma
**oportunidade comercial concreta**: um conhecido do Caio é **amigo pessoal do Cariani** (rede de academias de
grande porte), e a proposta é **incluir a ferramenta na rede e vender nacionalmente**.

O pedido de **15/07** é explícito quanto à **prioridade**: *fazer o prompt de academia (agachamento) **antes** de
voltar à calibração do beach tennis*, para ter uma **demo/POC para enviar ao cliente interessado**.

## 2. O problema, na voz do cliente

- **Quer**: um "prompt básico" de **análise postural do agachamento** que produza um **relatório do atleta**
  (execução + pontos a melhorar), pronto para **mostrar ao cliente** — `00000517`, `00000523`.
- **Por quê agora**: oportunidade com a **rede Cariani**; "colocar em todas as academias do Cariani do Brasil" e
  **vender nacionalmente** — `00000519`, `00000523`.
- **Ambição**: estender o prompt para **todos os exercícios de academia** (não só agachamento; bíceps citado) —
  `00000519`, `00000523`.
- **Prioridade**: **academia primeiro**, beach tennis depois ("antes de mexer na questão do bit… depois a gente
  regula o bit") — `00000523`.

## 3. Por que este pivô é viável (avaliação técnica do próprio Mateus)

Mateus avalia (`00000518`) que **academia é MAIS viável que beach tennis**:

| Fator | Beach tennis (atual) | Academia (pivô) |
|---|---|---|
| Velocidade do movimento | Rápido | **Lento / controlado** |
| Rastreio de bola | Necessário | **Não se aplica** |
| Rastreio/identificação do usuário em quadra | Crítico e difícil (vários atletas, lados, câmera ao fundo) | **Simplificado** (1 pessoa, enquadramento próximo) |
| Repetição do gesto | Variável | **Cíclica e previsível** (séries/repetições) |
| Esforço de calibração | Alto (meta 95% ainda não batida) | **Menor** — "relativamente mais tranquilo" |

**Custo estrutural sinalizado**: "**teria que duplicar o projeto**" (`00000518`) — ou seja, uma **nova
vertical/variante** do produto, reaproveitando o motor mas com domínio, prompts, parâmetros e relatório próprios.

## 4. O motor reutilizável (ativo que habilita o pivô)

Capacidades já existentes (fundamento — `00000012`, `00000013`, `00000014`):

1. **Ingestão de vídeo** enviado pelo usuário (página web de upload — POC).
2. **Segmentação** do vídeo em partes/trechos para análise criteriosa.
3. **Compreensão multimodal** — entende imagem (frame a frame) e áudio do vídeo.
4. **Análise guiada por metodologia** — critérios de um domínio (oratória → esporte → academia) embutidos no prompt/IA.
5. **Relatório de feedback** — diagnóstico + recomendações de melhoria.
6. **Padrão de evolução / trackeamento** — comparar vídeos ao longo do tempo para medir progresso.

O pivô = **trocar o domínio de análise** (esporte → biomecânica de exercícios) mantendo o pipeline
ingestão → segmentação → análise → relatório → evolução.

## 5. Impacto e stakeholders

- **Caio Bitvar** — dono da visão de produto/negócio; conduz a relação comercial (Cariani, "o rapaz", Juca, Du).
- **Mateus | illumi** — desenvolvimento do motor e das variantes; avalia viabilidade técnica.
- **Cliente/rede (Cariani)** — canal de distribuição-alvo; academias e seus alunos/personais como usuários finais.
- **Praticante (aluno)** e **treinador/personal** — usuários finais do relatório de execução.

## 6. Restrições, riscos e lacunas

- **R1 — Insumo ausente**: os **vídeos de agachamento** citados **não estão neste export** (todos os vídeos
  verificados são beach tennis). É preciso obter as filmagens de referência antes da POC. → **Q-01**.
- **R2 — Metodologia biomecânica indefinida**: não há, nas mensagens, os **critérios técnicos** do agachamento
  (profundidade, alinhamento joelho-pé, coluna neutra, valgo, etc.). Precisa de fonte especialista. → **Q-02**.
- **R3 — Escopo "todos os exercícios"** é amplo; a 1ª entrega deve ser **agachamento apenas** (MVP) para não
  travar a demo. → tratado no escopo dos Requisitos.
- **R4 — Conflito de prioridade** com a calibração do beach tennis; decisão do Caio (15/07) é **academia primeiro**.
- **R5 — Disponibilidade da Illumi**: Mateus sinaliza agenda ocupada com outro projeto (João Agro) e time sem
  folga para delegar (`00000515`, `00000520`) — risco de prazo para a janela comercial.
- **R6 — Requisitos de captura** (ângulo de câmera, enquadramento, iluminação, roupa) ainda não definidos para
  academia — herdam a lição do beach tennis de padronizar o recorte de entrada (`00000436`). → **Q-03**.

## 7. Próximas ações recomendadas

1. Obter do Caio os **vídeos de agachamento** de referência (masc/fem, ângulos) — desbloqueia **Q-01**.
2. Definir a **metodologia de avaliação do agachamento** (checklist biomecânico) com um especialista — **Q-02**.
3. Construir a **POC de academia** fora da plataforma (página de upload isolada, como no beach tennis) para gerar
   **1 relatório de agachamento** demonstrável ao cliente.
4. Validar o relatório com o Caio/especialista antes de enviar ao contato da rede Cariani.
5. Só então retomar a calibração do beach tennis.

> Especificação detalhada (requisitos funcionais/não funcionais, regras, entidades, cenários de aceitação e
> questões em aberto) em [`Requisitos-pivo-academia.md`](./Requisitos-pivo-academia.md).
