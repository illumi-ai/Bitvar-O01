"""Prompts das três chamadas (blueprint §03, §07).

* ``analysis_system_prompt(gender, mode)`` — instrução da chamada 1 (vídeo→JSON);
* ``build_narrative_prompt(metrics, gender, mode)`` — chamada 2 (JSON→texto PT-BR);
* ``build_tts_prompt(narrative)`` — chamada 3 (texto→áudio), com preâmbulo
  anti-falso-bloqueio do classificador.
"""

from __future__ import annotations

import json

from .benchmarks import benchmark_block

_GENDER_PT = {"male": "masculino", "female": "feminino"}

_CLIP_PROMPT = """\
Você é um treinador e analista de biomecânica de tênis de elite. Recebe um CLIPE
curto (lance individual) de um jogador do tênis {gender_pt}. O vídeo foi amostrado
a 4 quadros por segundo em alta resolução — você consegue ver preparação, contato
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
- "phase_confidence" e "shot_confidence": "baixa" | "media" | "alta". Use "baixa"
  quando o frame do contato não estiver nítido (o clipe roda a 4 fps, o instante
  exato pode cair entre quadros).

PASSO 2 — TRAVE NA PESSOA CERTA.
- Se houver uma identificação do jogador-alvo abaixo, analise SOMENTE essa pessoa.
- "subject_lock_confidence": quão certo você está de ter analisado o atleta-alvo
  (e não o parceiro/adversário). Se dois jogadores tiverem aparência parecida, use
  "baixa" e diga isso na "visual_evidence".
- "handedness": "destro" | "canhoto" | "indeterminado", pela mão que segura a raquete.

PASSO 3 — POSICIONAMENTO (qualitativo, nunca coordenadas).
- "positioning.observed_zone"/"observed_side": onde o atleta ESTAVA — zona
  ("fundo"|"meio"|"rede"|"transicao") e lado ("esquerda"|"centro"|"direita").
- "positioning.recommended_zone"/"recommended_side": a MELHOR posição que ele
  deveria ocupar naquele contexto, com "rationale" curto em PT-BR.
- Use apenas zonas relativas e grosseiras. Se a perspectiva não permitir, deixe nulo.

PASSO 4 — EXECUÇÃO TÉCNICA (granularidade de treinador).
- "analysis_mode" deve ser "clip"; "gender_profile" deve ser "{gender}".
- Para cada dimensão, dê "score" inteiro de 0 a 10 e uma "observation" em PT-BR no
  formato de treinador: SEGMENTO corporal + FASE + CONSEQUÊNCIA no golpe (ex.:
  "perna de trás lenta na bola curta, você chega atrasado e bate em desequilíbrio";
  "antecipação ao centro tardia, a raquete sobe depois do contato"). Aponte o
  micro-detalhe visível, não rótulos genéricos.
- Pontue com critério: 0-3 deficiente, 4-6 mediano, 7-8 bom, 9-10 nível de elite.
- SEMPRE preencha "footwork_and_movement" e "biomechanics" — são o foco do
  micro-detalhe de movimento que o treinador procura.
- "clip_quality_score" (0-10) é a nota técnica global ponderada do lance.
- "key_improvement" é a ÚNICA correção mais importante e acionável — a entrega
  central. Ancore-a na FASE e na POSIÇÃO quando relevante (ex.: "na recepção do
  lado esquerdo, recue meio passo antes do split step"). Seja específico, em PT-BR.
- "secondary_improvements": até 3 ajustes secundários.

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
do tênis {gender_pt} (amostrado a 1 quadro por segundo). Sua tarefa: extrair a
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
    hair: str | None = None,
) -> str | None:
    """Bloco de identificação do jogador a analisar (ANCORAGEM POR APARÊNCIA).

    O take costuma ter várias pessoas; estes atributos **não-biométricos** (roupa,
    lateralidade, boné, raquete, óculos) deixam claro QUEM analisar e fixam uma
    convenção de rótulo para o modelo seguir a mesma pessoa entre os frames, mesmo
    que ela troque de lado da quadra. NÃO é reconhecimento facial: o "nome" é apenas
    um rótulo de saudação, jamais derivado do rosto. Retorna None se nada foi informado.
    """
    parts = []
    if name and name.strip():
        parts.append(f"Nome (apenas para se dirigir ao atleta): {name.strip()}")
    if outfit and outfit.strip():
        parts.append(f"Roupa/aparência: {outfit.strip()}")
    if side and side.strip():
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
    return (
        "IDENTIFICAÇÃO DO JOGADOR A ANALISAR — o vídeo pode conter VÁRIAS pessoas. "
        "Fixe esta convenção logo no início e SIGA A MESMA PESSOA por estes atributos "
        "de aparência em todos os quadros, mesmo que ela mude de lado da quadra entre "
        "games. Analise SOMENTE este jogador e ignore os demais (adversário, parceiro, "
        "pessoas ao fundo). Se dois jogadores tiverem aparência parecida e você ficar "
        "em dúvida, baixe 'subject_lock_confidence' e registre isso em 'visual_evidence'. "
        "Identifique pela APARÊNCIA descrita — nunca por reconhecimento facial:\n- "
        + "\n- ".join(parts)
    )


def build_camera_block(camera_position: str | None) -> str | None:
    """Convenção espacial ancorada na câmera (spec B1, feedback Caio 13/06).

    Registra qual câmera embasa a leitura e, para a câmera central (atrás do
    fundo), fixa o mapeamento "lado esquerdo do vídeo ↔ lado direito da quadra".
    Sempre em zonas qualitativas — sem coordenadas. Retorna None se não informado.
    """
    pos = (camera_position or "").strip().lower()
    if not pos:
        return None
    if pos in ("central", "centro", "center", "centre"):
        return (
            "REFERÊNCIA DE CÂMERA: filmado pela CÂMERA CENTRAL, atrás da linha de fundo. "
            "Trate o lado ESQUERDO do vídeo como o lado DIREITO da quadra (do ponto de "
            "vista do jogador) e vice-versa. Use sempre zonas qualitativas "
            "(fundo/meio/rede, esquerda/centro/direita) — nunca coordenadas exatas. "
            "Se os jogadores trocarem de lado entre games, reoriente a leitura."
        )
    return (
        f"REFERÊNCIA DE CÂMERA: posição '{camera_position.strip()}'. Descreva a posição "
        "do atleta em zonas qualitativas relativas a esta câmera (fundo/meio/rede, "
        "esquerda/centro/direita) — nunca como coordenadas ou medidas exatas."
    )


def analysis_system_prompt(
    gender: str, mode: str,
    subject_block: str | None = None, camera_block: str | None = None,
) -> str:
    """System prompt da chamada 1, por (gênero × modo), com identificação e câmera opcionais."""
    gender_pt = _GENDER_PT[gender]
    if mode == "clip":
        prompt = _CLIP_PROMPT.format(gender=gender, gender_pt=gender_pt)
    else:
        prompt = _MATCH_PROMPT.format(
            gender=gender, gender_pt=gender_pt, benchmarks=benchmark_block(gender)
        )
    if camera_block:
        prompt += "\n\n" + camera_block
    if subject_block:
        prompt += "\n\n" + subject_block
    return prompt


def build_narrative_prompt(
    metrics: dict, gender: str, mode: str, player_name: str | None = None
) -> str:
    """Prompt da chamada 2: transforma o JSON em narrativa de coach falável."""
    gender_pt = _GENDER_PT[gender]
    foco = (
        "a execução técnica do lance (preparação, contato, finalização, footwork)"
        if mode == "clip"
        else "o desempenho na partida (saque, devolução, rally, winners/erros, break points)"
    )
    nome = (player_name or "").strip()
    saudacao = (
        f"Dirija-se ao atleta pelo primeiro nome ({nome}) ao menos uma vez, de forma natural.\n"
        if nome else ""
    )
    metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2)
    return f"""\
Você é um treinador de tênis experiente, direto e encorajador. A seguir está a
análise estruturada (JSON) de {foco} de um jogador do tênis {gender_pt}.
{saudacao}

Escreva um RELATÓRIO DE COACH em português do Brasil, em PROSA CORRIDA, pronto para
ser FALADO em voz alta (será convertido em áudio). Regras:
- Sem markdown, sem listas, sem símbolos, sem ler nomes de campos do JSON.
- Tom caloroso e motivador, mas honesto e específico; fale direto com o atleta ("você").
- Comece reconhecendo 1 ou 2 pontos fortes concretos (cite números/observações reais).
- Depois explique com clareza a correção principal (o "key_improvement") e por que ela importa.
- Mencione 1 ou 2 ajustes secundários se houver.
- Use os números da análise de forma natural (ex.: "seu primeiro saque ganhou cerca de 68%").
- Se houver "action_phase", diga naturalmente em que fase do ponto o atleta estava
  (ex.: "na recepção do saque") em vez de assumir um golpe ofensivo.
- Se houver "positioning", mencione a correção de posicionamento (onde estava × onde
  deveria estar) de forma natural.
- Inclua 1 ou 2 micro-detalhes biomecânicos concretos das observações (ex.: timing de
  perna, antecipação), no tom de quem aponta o detalhe que o atleta não percebe.
- Feche com uma frase curta de incentivo.
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
