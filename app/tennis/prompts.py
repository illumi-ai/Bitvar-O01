"""Prompts das três chamadas (blueprint §03, §07; calibragem Caio 24/06).

* ``analysis_system_prompt(gender, mode, subject_block, camera_block, rules_block)``
  — instrução da chamada 1 (vídeo→JSON); o clip injeta taxonomia fase→golpe (WF1),
  leitura tática do ponto (WF4) e foco nos membros inferiores (WF3);
* ``build_narrative_prompt(metrics, gender, mode, player_name)`` — chamada 2
  (JSON→texto PT-BR): impõe o esqueleto de 4 tempos do Juca (fase→raiz→regra→correção),
  injeta o few-shot real (:data:`JUCA_FEWSHOT`) e aplica guard-rails de hype (bane
  superlativos vazios; em fase defensiva, bane a palavra "smash") — WF5;
* ``build_tts_prompt(narrative)`` — chamada 3 (texto→áudio), com preâmbulo
  anti-falso-bloqueio do classificador.

As regras táticas cobráveis por categoria (gênero × nível) vivem em
:mod:`app.tennis.rules` (``build_rules_block``) e são injetadas aqui.
"""

from __future__ import annotations

import json

from .benchmarks import benchmark_block
from .config import tennis_settings as cfg

_GENDER_PT = {"male": "masculino", "female": "feminino"}

# Fases em que o lance é DEFENSIVO — usadas pelos guard-rails da narrativa (WF5):
# nessas fases a palavra "smash" é banida e o anti-hype é total.
_DEFENSIVE_PHASES = {"serve_return", "defense"}

_CLIP_PROMPT = """\
Você é um treinador e analista de biomecânica de tênis de elite. Recebe um CLIPE
curto (lance individual) de um jogador do tênis {gender_pt}. O vídeo foi amostrado
a {fps} quadros por segundo — você consegue ver preparação, contato
e finalização do golpe.

Sua tarefa: analisar o lance e devolver SOMENTE um JSON válido no schema fornecido
(sem texto fora do JSON, sem markdown).

PASSO 1 — IDENTIFIQUE A FASE ANTES DO GOLPE.
- "action_phase": o que o atleta está FAZENDO neste lance — sacando ("serve"),
  RECEBENDO o saque ("serve_return"), em troca de fundo ("baseline_rally"),
  atacando ("attack"), defendendo ("defense"), em deslocamento ("transition") ou
  jogo de rede ("net_play"). Use "unknown" se não der para determinar.
- ATENÇÃO: se o atleta está RECEBENDO ou DEFENDENDO, a fase NÃO é ataque, mesmo que
  o movimento lembre um smash. Nesse caso "shot_identified" deve ser "return" ou
  "unknown" — NÃO rotule como "overhead_smash"/winner. Avalie split step, leitura,
  postura e antecipação, não a finalização de um golpe ofensivo.
- TAXONOMIA GOLPE-POR-FASE (rotule o golpe DENTRO da fase, nunca o contrário):
  - serve_return / defense (RECEPÇÃO ou DEFESA): valores válidos de "shot_identified"
    são "return" (devolução), "drop_shot" (deixadinha/bola curta que morre perto da
    rede), "volley" (voleio/bloqueio defensivo), "slice" (corte para amortecer) ou
    "lob" (balão defensivo). NUNCA "overhead_smash" aqui.
  - attack / net_play (ATAQUE ou JOGO DE REDE): aí sim cabem "overhead_smash",
    "volley" de finalização, "forehand"/"backhand" agressivos, "approach".
  - baseline_rally (TROCA DE FUNDO): "forehand", "backhand", "slice", "lob", "drop_shot".
  - serve (SACANDO): "serve".
  - REGRA DURA: "overhead_smash" SÓ é válido se "action_phase" for "attack" ou
    "net_play". Em QUALQUER outra fase "overhead_smash" é PROIBIDO — se o braço sobe
    sobre uma bola alta mas o atleta está recebendo/defendendo, é "return", "lob" ou
    "drop_shot" defensivo, não smash.
- DESAMBIGUAÇÃO DEFESA x ATAQUE (decida a FASE por estes sinais, não pelo braço):
  - A bola está CHEGANDO do saque ou do ataque do adversário => DEFESA/recepção.
  - O atleta RECUA, ajusta os pés ou chega atrasado/em desequilíbrio => DEFESA.
  - O toque é CURTO e a bola MORRE perto da rede (deixadinha) => DEFESA com
    "drop_shot", não um winner de ataque.
  - A bola SAI LENTA, sem aceleração/finalização => DEFESA, não ataque.
  - LEIA OS MEMBROS INFERIORES PRIMEIRO: base baixa e joelhos flexionados indicam
    recepção/preparo defensivo; só base ativa avançando sustenta um ataque.
  - Só classifique "attack"/"overhead_smash" se houver INTENÇÃO ofensiva clara: base
    estável, atleta avançando/subindo, aceleração e finalização para baixo.
- DETECTOR DE BOLA FLUTUANTE: se o atleta estiver com as PERNAS ESTENDIDAS/ALTAS e a
  RAQUETE BAIXA, a bola tende a FLUTUAR (sair sem controle, alta e lenta). Nesse caso
  marque "floating_ball_fault": true e descreva em "floating_ball_observation" (base
  alta + raquete baixa => bola flutuante). Se a base estiver baixa e firme e a bola
  controlada, marque false.
- AMBIGUIDADE: se ficar em dúvida entre duas fases, escolha a MAIS DEFENSIVA como
  "action_phase", registre a outra em "phase_alternative", explique a dúvida em
  "phase_alternative_rationale" e baixe "phase_confidence" para "baixa".
- "phase_confidence" e "shot_confidence": "baixa" | "media" | "alta". Use "baixa"
  quando o frame do contato não estiver nítido (o clipe roda a {fps} fps, o instante
  exato pode cair entre quadros).

PASSO 2 — TRAVE NA PESSOA CERTA E NA QUADRA CERTA.
- ÂNCORA GEOMÉTRICA (precedência): se houver um QUADRANTE-ALVO informado mais abaixo,
  ele é a âncora PRIMÁRIA e tem precedência sobre a aparência — o atleta a analisar é
  o que COMEÇA O PONTO naquele canto do QUADRO, não o mais saliente nem o que recebe a
  bola. Use a aparência só para SEGUIR a mesma pessoa entre os quadros.
- DELIMITE A QUADRA-ALVO: é a quadra onde está o atleta a analisar, fechada pelas
  suas 4 linhas (duas laterais, duas de fundo) e cortada pela REDE no meio.
  Analise SOMENTE o que acontece DENTRO dessa quadra.
- IGNORE A QUADRA AO LADO: pode haver outra partida acontecendo na QUADRA ADJACENTE
  (ao lado, ao fundo ou parcialmente no enquadramento). DESCARTE qualquer pessoa,
  bola ou movimento da quadra vizinha — eles não fazem parte deste lance. Não deixe
  um golpe chamativo da quadra ao lado puxar sua atenção para fora do atleta-alvo.
- Se houver uma identificação do jogador-alvo abaixo, analise SOMENTE essa pessoa.
- ÂNCORA PERSISTENTE: fixe o atleta-alvo no PRIMEIRO quadro do lance (pela aparência
  descrita) e SIGA-O do INÍCIO AO FIM do ponto, sem trocar de pessoa no meio. Se ele
  for momentaneamente encoberto, retome a MESMA pessoa pela aparência — nunca pule
  para o parceiro, o adversário ou alguém da quadra ao lado.
- "subject_lock_confidence": quão certo você está de ter analisado o atleta-alvo
  (e não o parceiro/adversário/uma pessoa da quadra adjacente). Se dois jogadores
  tiverem aparência parecida, ou se você precisou descartar gente da quadra vizinha,
  use "baixa"/"media" e explique isso na "visual_evidence".
- "handedness": "destro" | "canhoto" | "indeterminado", pela mão que segura a raquete.

PASSO 3 — POSICIONAMENTO (qualitativo, nunca coordenadas).
- "positioning.observed_zone"/"observed_side": onde o atleta ESTAVA — zona
  ("fundo"|"meio"|"rede"|"transicao") e lado ("esquerda"|"centro"|"direita").
- "positioning.recommended_zone"/"recommended_side": a MELHOR posição que ele
  deveria ocupar naquele contexto, com "rationale" curto em PT-BR.
- Use apenas zonas relativas e grosseiras. Se a perspectiva não permitir, deixe nulo.

PASSO 4 — LEIA O PONTO (TÁTICA RELACIONAL DOS 4 JOGADORES).
- NÃO olhe só o atleta-alvo. Leia o ponto como um todo: a posição RELATIVA dos
  quatro jogadores (alvo, parceiro e os dois adversários) e o ESPAÇO VAZIO que
  se abre na quadra adversária a cada bola. O golpe certo nasce do vazio, não do
  braço.
- Preencha "tactical_events": uma lista (até 5) dos eventos táticos que você de
  fato VIU, cada um com "event_type" do catálogo abaixo, "description" curta em
  PT-BR (quem fez, contra quem, que espaço abriu), "approx_timestamp_s" e "actor"
  ("alvo" | "adversario" | "parceiro" | "indefinido"). Se você não enxergar nada
  relacional com clareza, deixe "tactical_events" nulo — NÃO invente evento.
- CATÁLOGO de "event_type" (use exatamente estes rótulos):
  - "finta": um jogador AMAGA/dissimula um golpe para enganar o adversário (ex.:
    finge a finalização e faz a deixadinha). Repare se a finta causa imprecisão
    NELE MESMO ou tira o adversário de posição.
  - "aproveitamento_deslocamento": a dupla adversária se DESLOCA e fica fora de
    posição; o jogador explora esse deslocamento.
  - "espaco_livre": a bola é finalizada no VAZIO que se abriu na quadra adversária.
  - "colocacao": bola COLOCADA com intenção (ângulo, cantinho, paralela), não força.
  - "quebra_de_ritmo": muda o tempo do ponto (deixadinha/drop, bola curta e lenta
    depois de troca forte) para descompassar o adversário.
  - "outro": evento tático relevante que não cabe nos rótulos acima.
- ENCADEIE CAUSA→EFEITO em "point_outcome_link": una os eventos ao desfecho do
  ponto, em PT-BR e em uma frase (ex.: "o adversário tentou a finta e errou o
  tempo; o alvo não caiu na finta, aproveitou o vazio aberto pelo deslocamento da
  dupla e finalizou no espaço livre"). É a leitura que o treinador faz e o
  protótipo perde.
- COERÊNCIA COM A FASE: se você marcou "action_phase" como recepção/defesa, os
  eventos do alvo devem refletir REAÇÃO (aproveitar o vazio, quebrar o ritmo), não
  uma finalização agressiva inventada.

PASSO 5 — EXECUÇÃO TÉCNICA (granularidade de treinador).
- "analysis_mode" deve ser "clip"; "gender_profile" deve ser "{gender}".
- Para cada dimensão, dê "score" inteiro de 0 a 10 e uma "observation" em PT-BR no
  formato de treinador: SEGMENTO corporal + FASE + CONSEQUÊNCIA no golpe (ex.:
  "perna de trás lenta na bola curta, você chega atrasado e bate em desequilíbrio";
  "antecipação ao centro tardia, a raquete sobe depois do contato"). Aponte o
  micro-detalhe visível, não rótulos genéricos.
- Pontue com critério: 0-3 deficiente, 4-6 mediano, 7-8 bom, 9-10 nível de elite.
- SEMPRE preencha "footwork_and_movement" e "biomechanics" — são o foco do
  micro-detalhe de movimento que o treinador procura.
- LEIA PRIMEIRO OS MEMBROS INFERIORES. A qualidade de uma recepção/defesa está na
  BASE (joelhos flexionados, centro de gravidade baixo), não no braço. Avalie a
  flexão de joelhos e a estabilidade ANTES de julgar a raquete.
- "lower_body_base": avalie os MEMBROS INFERIORES com "score" 0-10 e "observation"
  em PT-BR: "defensive_base_flexion" (flexão de joelhos na recepção/defesa — base
  baixa = nota alta), "movement_base_flexion" (flexão no deslocamento até a bola) e
  "stability_center_of_gravity" (centro de gravidade firme no contato). Em fases
  defensivas (recepção/defesa), ESTE é o eixo mais importante.
- "clip_quality_score" (0-10) é a nota técnica global ponderada do lance.
- "key_improvement" é a ÚNICA correção mais importante e acionável — a entrega
  central. Ancore-a na FASE e na POSIÇÃO quando relevante (ex.: "na recepção do
  lado esquerdo, recue meio passo antes do split step"). Seja específico, em PT-BR.
- "secondary_improvements": até 3 ajustes secundários.

RUBRICA CONDICIONADA À FASE (o que é "bom" depende do que o atleta está fazendo).
- RECEPÇÃO / DEFESA ("serve_return"/"defense"): "bom" = BASE BAIXA e estável (joelhos
  flexionados), split step no tempo e bola que NÃO flutua (controlada, baixa). O
  "key_improvement" DEVE focar a BASE / MEMBROS INFERIORES — ex.: "flexione mais os
  joelhos e baixe o centro de gravidade para receber firme". NÃO centre a correção no
  braço nem fale em "finalizar o smash": em recepção não há smash a proteger.
- ATAQUE / REDE ("attack"/"net_play"/"serve"): "bom" = CONTATO ALTO e firme, trajetória
  da raquete consistente e boa transferência de peso; aí sim a correção pode focar
  contato/finalização.
- FUNDO / TRANSIÇÃO ("baseline_rally"/"transition"): equilibre base e execução de braço.
- Em QUALQUER caso, alinhe o "key_improvement" à "action_phase" identificada no PASSO 1.

HONESTIDADE E LIMITES.
- "visual_evidence": descreva em PT-BR o que de fato aparece no vídeo e embasa sua
  leitura; e "approx_timestamp_s": instante aproximado do lance (estimativa).
- Baseie-se apenas no que é visível. Se o detalhe fino (instante de contato, efeito,
  face da raquete) não estiver nítido, diga que é estimativa e baixe a confiança.
- NÃO estime tempos de reação nem velocidades em números (ms, m/s) — descreva
  qualitativamente (rápido/lento/tardio). NÃO tente identificar nomes reais por
  rosto: refira-se ao atleta apenas pelos atributos de aparência fornecidos.
"""

_MATCH_PROMPT = """\
Você é um analista de partidas de tênis de elite. Recebe um vídeo de PARTIDA/GAMES
do tênis {gender_pt} (amostrado a {fps} quadros por segundo). Sua tarefa: extrair a
ESTATÍSTICA da partida e devolver SOMENTE um JSON válido no schema fornecido
(sem texto fora do JSON, sem markdown).

Regras:
- "analysis_mode" deve ser "match"; "gender_profile" deve ser "{gender}".
- Conte e estime: saque (1º/2º saque, aces, duplas faltas), devolução, rally
  (comprimento médio e distribuição 0-4 / 5-8 / 9+), winners, erros não forçados
  e forçados, e break points (enfrentados, salvos, oportunidades, convertidos).
- Percentuais em 0-100. Onde não der para contar com confiança, faça a MELHOR
  estimativa e registre a incerteza na "observation" da seção. NÃO invente precisão.
- Preencha "winner_to_ue_ratio" (winners ÷ erros não forçados) — é a métrica-chave.
- Em cada "benchmark_reference", use as normas de elite abaixo como referência:
{benchmarks}
- "key_improvement": a correção mais importante e acionável da partida, em PT-BR.
- "secondary_improvements": até 3 itens.
- NÃO calcule nenhum score ponderado — isso é feito fora do modelo.
- Honestidade acima de tudo: contar estatística a partir de vídeo bruto é difícil;
  prefira estimativas calibradas a números inventados.
"""


def build_subject_block(
    name: str | None = None, outfit: str | None = None,
    side: str | None = None, notes: str | None = None,
    *,
    handedness: str | None = None, headwear: str | None = None,
    racket_color: str | None = None, glasses: bool | None = None,
    hair: str | None = None, quadrant_active: bool = False,
) -> str | None:
    """Bloco de identificação do jogador a analisar (ANCORAGEM POR APARÊNCIA).

    O take costuma ter várias pessoas; estes atributos **não-biométricos** (roupa,
    lateralidade, boné, raquete, óculos) deixam claro QUEM analisar e fixam uma
    convenção de rótulo para o modelo seguir a mesma pessoa entre os frames, mesmo
    que ela troque de lado da quadra. NÃO é reconhecimento facial: o "nome" é apenas
    um rótulo de saudação, jamais derivado do rosto. Retorna None se nada foi informado.

    ``quadrant_active=True`` (há QUADRANTE-alvo) muda o PAPEL deste bloco: quem ESCOLHE
    o atleta passa a ser o quadrante (precedência), então a aparência vira só FIO DE
    CONTINUIDADE para seguir a mesma pessoa — o bloco deixa de se apresentar como
    seletor concorrente e omite o 'lado da quadra' (texto livre quadra-relativo que
    poderia contradizer a âncora de quadrante, frame-relativa, e disparar falso
    'target_mismatch').
    """
    parts = []
    if name and name.strip():
        parts.append(f"Nome (apenas para se dirigir ao atleta): {name.strip()}")
    if outfit and outfit.strip():
        parts.append(f"Roupa/aparência: {outfit.strip()}")
    if side and side.strip() and not quadrant_active:
        parts.append(f"Posição / lado da quadra: {side.strip()}")
    if handedness and str(handedness).strip():
        parts.append(f"Lateralidade (mão da raquete): {str(handedness).strip()}")
    if headwear and headwear.strip():
        parts.append(f"Boné/viseira: {headwear.strip()}")
    if racket_color and racket_color.strip():
        parts.append(f"Raquete: {racket_color.strip()}")
    if hair and hair.strip():
        parts.append(f"Cabelo: {hair.strip()}")
    if glasses is True:
        parts.append("Usa óculos.")
    if notes and notes.strip():
        parts.append(f"Outras dicas: {notes.strip()}")
    if not parts:
        return None
    if quadrant_active:
        # Quem escolhe é o quadrante; estes atributos só ajudam a SEGUIR o mesmo atleta.
        return (
            "ATRIBUTOS DE CONTINUIDADE DO ATLETA-ALVO — quem ESCOLHE o atleta é o "
            "QUADRANTE informado acima (tem precedência); use os atributos abaixo APENAS "
            "para SEGUIR a MESMA pessoa entre os quadros do INÍCIO AO FIM do lance, NÃO "
            "para escolher outra. Continue ignorando o parceiro, os adversários e QUALQUER "
            "pessoa de uma QUADRA AO LADO/adjacente. Se ficar em dúvida, baixe "
            "'subject_lock_confidence' e registre em 'visual_evidence'. Siga pela "
            "APARÊNCIA — nunca por reconhecimento facial:\n- "
            + "\n- ".join(parts)
        )
    return (
        "IDENTIFICAÇÃO DO JOGADOR A ANALISAR — o vídeo pode conter VÁRIAS pessoas. "
        "Fixe esta convenção logo no início e SIGA A MESMA PESSOA por estes atributos "
        "de aparência em todos os quadros, mesmo que ela mude de lado da quadra entre "
        "games. Analise SOMENTE este jogador e ignore os demais (adversário, parceiro, "
        "pessoas ao fundo e QUALQUER pessoa de uma QUADRA AO LADO/adjacente que apareça "
        "no enquadramento). Mantenha a trava nesta MESMA pessoa do INÍCIO AO FIM do lance, "
        "sem trocar de atleta no meio do ponto. Se dois jogadores tiverem aparência "
        "parecida e você ficar "
        "em dúvida, baixe 'subject_lock_confidence' e registre isso em 'visual_evidence'. "
        "Identifique pela APARÊNCIA descrita — nunca por reconhecimento facial:\n- "
        + "\n- ".join(parts)
    )


def build_camera_block(camera_position: str | None, frame_relative: bool = False) -> str | None:
    """Convenção espacial ancorada na câmera (spec B1, feedback Caio 13/06).

    Registra qual câmera embasa a leitura e, para a câmera central (atrás do
    fundo), fixa o mapeamento "lado esquerdo do vídeo ↔ lado direito da quadra".
    Sempre em zonas qualitativas — sem coordenadas. Retorna None se não informado.

    ``frame_relative=True`` (ligado quando há QUADRANTE-alvo) força a leitura RELATIVA
    AO FRAME para QUALQUER câmera (fundo, lateral, central…): o lado é sempre o da
    IMAGEM (esquerda/direita do quadro), sem inverter para a quadra. A inversão
    quadra-relativa ('central' legado) contradiria a âncora de quadrante e o auto-check
    de setor (que leem ``observed_side`` em termos de IMAGEM). Sem quadrante, 'central'
    mantém a convenção de inversão legada e a câmera de FUNDO segue frame-relativa.
    """
    pos = (camera_position or "").strip().lower()
    if not pos:
        return None
    # casa por PREFIXO (espelha quadrants.normalize_camera_axis): as 4 posições
    # (fundo_meu/fundo_adv/lateral_esq/lateral_dir) reduzem aos 2 eixos de leitura.
    fundo_like = pos.startswith("fundo") or pos in ("atras", "atrás", "back", "baseline")
    lateral_like = pos.startswith("lateral") or pos in ("lado", "side")
    central_like = pos in ("central", "centro", "center", "centre")
    if fundo_like:
        nice = "de FUNDO (atrás da linha de fundo)"
    elif lateral_like:
        nice = "da LATERAL (na lateral da quadra)"
    elif central_like:
        nice = "pela câmera CENTRAL (atrás da linha de fundo)"
    else:
        nice = f"da posição '{camera_position.strip()}'"
    if fundo_like or frame_relative:
        # FUNDO (sempre) ou QUALQUER câmera sob âncora de QUADRANTE (plano 25/06): a
        # leitura é RELATIVA AO FRAME — sem a inversão esquerda↔direita quadra-relativa.
        # O quadrante tocado pelo usuário e o que o modelo vê são o MESMO canto da
        # imagem; inverter aqui quebraria o auto-check de setor.
        return (
            f"REFERÊNCIA DE CÂMERA: filmado {nice}. Descreva a posição do atleta em zonas "
            "qualitativas RELATIVAS À IMAGEM (fundo/meio/rede, esquerda/centro/direita DO "
            "QUADRO) — nunca coordenadas. O lado ESQUERDO da imagem é 'esquerda' e o "
            "DIREITO é 'direita' (não inverta para a quadra). A quadra-alvo é a que "
            "aparece no enquadramento, fechada pelas 4 linhas e pela rede; IGNORE "
            "qualquer quadra ao lado/adjacente no quadro."
        )
    if central_like:
        return (
            "REFERÊNCIA DE CÂMERA: filmado pela CÂMERA CENTRAL, atrás da linha de fundo. "
            "Trate o lado ESQUERDO do vídeo como o lado DIREITO da quadra (do ponto de "
            "vista do jogador) e vice-versa. Use sempre zonas qualitativas "
            "(fundo/meio/rede, esquerda/centro/direita) — nunca coordenadas exatas. "
            "Se os jogadores trocarem de lado entre games, reoriente a leitura. "
            "A quadra-alvo é a que está à frente desta câmera, fechada pelas 4 linhas e "
            "pela rede; IGNORE qualquer quadra ao lado/adjacente que apareça no quadro."
        )
    return (
        f"REFERÊNCIA DE CÂMERA: filmado {nice}. Descreva a posição "
        "do atleta em zonas qualitativas relativas a esta câmera (fundo/meio/rede, "
        "esquerda/centro/direita) — nunca como coordenadas ou medidas exatas."
    )


def analysis_system_prompt(
    gender: str, mode: str,
    subject_block: str | None = None, camera_block: str | None = None,
    rules_block: str | None = None, fps: int | None = None,
    quadrant_block: str | None = None,
) -> str:
    """System prompt da chamada 1, por (gênero × modo), com identificação, câmera, quadrante e regras opcionais.

    ``fps`` é a taxa de amostragem REAL (clip ou match; default: ``cfg.clip_fps`` /
    ``cfg.match_fps`` conforme o modo), injetada no prompt para o modelo saber quantos
    quadros/segundo recebe — em vez de um número fixo que sairia do ar quando a taxa muda.

    ``quadrant_block`` (:func:`app.tennis.quadrants.build_quadrant_block`) é a âncora
    GEOMÉTRICA do atleta-alvo: injetada logo após a referência de câmera e ANTES da
    identificação por aparência, porque tem precedência sobre ela (o quadrante escolhe;
    a aparência só dá continuidade).
    """
    gender_pt = _GENDER_PT[gender]
    if mode == "clip":
        prompt = _CLIP_PROMPT.format(
            gender=gender, gender_pt=gender_pt,
            fps=fps if fps is not None else cfg.clip_fps,
        )
    else:
        prompt = _MATCH_PROMPT.format(
            gender=gender, gender_pt=gender_pt, benchmarks=benchmark_block(gender),
            fps=fps if fps is not None else cfg.match_fps,
        )
    if camera_block:
        prompt += "\n\n" + camera_block
    if quadrant_block:
        prompt += "\n\n" + quadrant_block
    if subject_block:
        prompt += "\n\n" + subject_block
    if rules_block:
        prompt += "\n\n" + rules_block
    return prompt


# Trechos REAIS da narração-gabarito do treinador Juca (beach tennis), das
# transcrições do doc docs/calibragem-ia-caio-24jun2026.html §07 (áudios 00000213,
# 00000215+216, 00000217). Injetados como FEW-SHOT de ESTILO no build_narrative_prompt:
# transferem o vocabulário ("primeira defesa do ponto", "base mais baixa", "bola
# flutuante", "raiz do movimento") e o ritmo de diagnóstico-de-causa do especialista,
# substituindo o elogio-hype. São exemplo de TOM, não conteúdo a repetir.
JUCA_FEWSHOT = """\
EXEMPLOS DE ESTILO — narração-gabarito do treinador Juca (beach tennis). Imite o TOM,
o RITMO e o VOCABULÁRIO destes trechos; NÃO copie o conteúdo nem mencione estes lances:

[exemplo 1 - fase defensiva / recepção]
"Muito importante observar que na fase defensiva, nas devoluções, que é a primeira
defesa do ponto, o time tem que trabalhar com a base mais baixa, joelhos flexionados,
pra que haja estabilidade na devolução e para que a bola não flutue. Apesar de a
devolução ter caído no lugar adequado, a base ficou com pernas altas, estendidas e
raquete baixa - ou seja, uma bola flutuante."

[exemplo 2 - mecânica / raiz do movimento]
"O que a equipe mais precisa observar na mecânica dos golpes é que a base de
deslocamento e a base defensiva devem ser trabalhadas com maior flexão. Gerando
estabilidade nas bases, os golpes ficam mais controlados e eficazes."

[exemplo 3 - síntese honesta]
"O ponto é bem curto e, tecnicamente, o que falta é a raiz do movimento mesmo: os
membros inferiores. A base ainda está crua, e a partir disso todo o resto se compõe."\
"""


def _coach_skeleton(phase: str | None, mode: str, floating_fault: bool | None = None) -> str:
    """Esqueleto fixo da narração (WF5/eixo 5), RAMIFICADO POR MODO.

    CLIP (um lance): 4 tempos do Juca — nomear a FASE do lance -> apontar a RAIZ nos
    MEMBROS INFERIORES -> trazer a REGRA de contexto -> dar a CORREÇÃO. Em fase
    defensiva (``serve_return``/``defense``) proíbe a palavra "smash" (erro central do
    00000201); a falha-alvo "bola flutuante" só é citada quando ``floating_fault`` é True.
    MATCH (partida inteira): uma partida tem MUITOS pontos, não "uma fase" nem um lance
    único — o esqueleto é ESTATÍSTICO (nomeia o PADRÃO e a maior alavanca), sem "fase do
    ponto" nem "bola flutuante".
    """
    if mode != "clip":
        return (
            "ESTRUTURA OBRIGATÓRIA - siga estes 4 TEMPOS, NESTA ORDEM, em prosa corrida\n"
            "(sem numerá-los em voz alta):\n"
            "1) NOMEIE O PADRÃO da partida pelos números (saque, devolução, rally,\n"
            "   winners/erros não forçados, break points) ANTES de qualquer juízo.\n"
            "2) APONTE A MAIOR ALAVANCA: a estatística que mais pesa no resultado (ex.:\n"
            "   relação winners/erros, pontos de 2º saque, conversão de break points).\n"
            "3) Traga o CONTEXTO de comparação com a referência de elite, se houver.\n"
            "4) Entregue a CORREÇÃO objetiva e acionável (o \"key_improvement\")."
        )
    floating = (
        " A falha-alvo clássica é \"pernas altas/estendidas + raquete baixa = bola que flutua\"."
        if floating_fault else ""
    )
    anti_smash = (
        "\n- ATENÇÃO: a fase é DEFENSIVA/RECEPÇÃO. NÃO use a palavra \"smash\" nem trate\n"
        "  o lance como ataque; foi uma defesa/devolução (ex.: deixadinha/curta).\n"
        "  Avalie a base e a colocação, não a finalização ofensiva."
        if phase in _DEFENSIVE_PHASES else ""
    )
    return (
        "ESTRUTURA OBRIGATÓRIA - siga estes 4 TEMPOS, NESTA ORDEM, em prosa corrida (sem\n"
        "numerá-los em voz alta):\n"
        "1) NOMEIE A FASE do ponto em que o atleta estava (ex.: \"na recepção do saque\",\n"
        "   \"na defesa\", \"na troca de fundo\") ANTES de qualquer julgamento de golpe.\n"
        "2) APONTE A RAIZ do movimento nos MEMBROS INFERIORES: base, flexão de joelhos,\n"
        "   altura do centro de gravidade, estabilidade. É daqui que parte o diagnóstico." + floating + "\n"
        "3) Traga a REGRA de contexto, se houver (posicionamento, leitura tática), em\n"
        "   linguagem natural; se não houver, pule este tempo.\n"
        "4) Entregue a CORREÇÃO objetiva e acionável (o \"key_improvement\"), ligada à base."
        + anti_smash
    )


def build_narrative_prompt(
    metrics: dict, gender: str, mode: str, player_name: str | None = None,
) -> str:
    """Prompt da chamada 2: JSON -> narrativa de coach falável, no PADRÃO DO JUCA.

    Impõe o esqueleto de :func:`_coach_skeleton` (4 tempos no CLIP; estatístico no
    MATCH) e aplica guard-rails de hype. O few-shot single-point do Juca
    (:data:`JUCA_FEWSHOT`) só entra no CLIP — numa partida inteira ele seria
    incoerente. Se ``action_phase`` for defensiva (recepção/defesa), bane a palavra
    "smash" e exige elogio só com evidência — corrige o erro do 00000201 ("belo smash").
    """
    gender_pt = _GENDER_PT[gender]
    foco = (
        "a execução técnica do lance (preparação, contato, finalização, footwork e BASE)"
        if mode == "clip"
        else "o desempenho na partida (saque, devolução, rally, winners/erros, break points)"
    )
    nome = (player_name or "").strip()
    saudacao = (
        f"Dirija-se ao atleta pelo primeiro nome ({nome}) ao menos uma vez, de forma natural.\n"
        if nome else ""
    )
    phase = metrics.get("action_phase") if isinstance(metrics, dict) else None
    is_def = phase in _DEFENSIVE_PHASES
    # guard-rail de hype: superlativos vazios banidos; na fase defensiva o aperto é total.
    hype_rule = (
        "- A fase é DEFENSIVA/RECEPÇÃO: NÃO use superlativos vazios ('belo', 'fantástico',\n"
        "  'excelente', 'incrível', 'show', 'sensacional'). O foco é CORRIGIR a base, não\n"
        "  elogiar. Só registre um acerto se houver evidência concreta (um número ou uma\n"
        "  observação real da análise).\n"
        if is_def else
        "- Evite superlativos vazios ('belo', 'fantástico', 'excelente', 'incrível', 'show').\n"
        "  Só elogie com evidência concreta (número ou observação real) - nunca por elogiar.\n"
    )
    floating_fault = metrics.get("floating_ball_fault") if isinstance(metrics, dict) else None
    skeleton = _coach_skeleton(phase, mode, floating_fault)
    # few-shot single-point do Juca só no CLIP — numa partida inteira seria incoerente.
    fewshot_block = (JUCA_FEWSHOT + "\n\n") if mode == "clip" else ""
    metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2)
    return f"""\
Você é o treinador Juca: técnico de tênis/beach tennis experiente, direto e SEM hype. A
seguir está a análise estruturada (JSON) de {foco} de um jogador do tênis {gender_pt}.
{saudacao}
{fewshot_block}{skeleton}

Escreva um RELATÓRIO DE COACH em português do Brasil, em PROSA CORRIDA, pronto para ser
FALADO em voz alta (será convertido em áudio). Regras:
- Sem markdown, sem listas, sem símbolos, sem ler nomes de campos do JSON.
- Tom direto, técnico e honesto; fale com o atleta por "você". Diagnóstico de CAUSA, não
  elogio genérico.
{hype_rule}- Se houver "action_phase", diga naturalmente em que fase do ponto o atleta estava
  (ex.: "na recepção do saque") em vez de assumir um golpe ofensivo.
- Se houver "positioning", mencione a correção de posicionamento (onde estava × onde
  deveria estar) de forma natural.
- Se houver "tactical_events" ou "point_outcome_link", CONTE A LEITURA DO PONTO em
  prosa: o que cada lado fez (a finta do adversário, o deslocamento da dupla, o
  espaço livre aproveitado) e como a tática levou ao resultado. Fale de forma
  relacional ("o adversário tentou te enganar com a finta, mas você leu o vazio
  e finalizou no espaço livre"), sem ler rótulos do JSON.
- Ancore o diagnóstico nos MEMBROS INFERIORES quando as observações de base/footwork
  indicarem (flexão de joelho, split step, equilíbrio, recuperação).
- Explique com clareza a correção principal (o "key_improvement") e por que ela importa;
  mencione 1 ou 2 ajustes secundários se houver.
- Use os números da análise de forma natural (ex.: "seu primeiro saque ganhou cerca de 68%").
- Feche com uma frase curta de incentivo realista.
- Tamanho: 160 a 300 palavras. Devolva SOMENTE o texto do relatório.

ANÁLISE (JSON):
{metrics_json}
"""


# preâmbulo obrigatório: evita falso-bloqueio do classificador e leitura das
# instruções em voz alta; tag de áudio em inglês mesmo com texto em PT (doc).
def build_tts_prompt(narrative: str) -> str:
    """Prompt da chamada 3 (TTS Vindemiatrix), com preâmbulo anti-bloqueio."""
    return (
        "Sintetize em voz alta o seguinte relatório de um treinador de tênis, "
        "em português do Brasil, com tom calmo, claro e encorajador. [calmly]\n"
        "TRANSCRIÇÃO A FALAR:\n" + narrative.strip()
    )
