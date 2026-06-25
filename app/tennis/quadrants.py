"""Seleção do atleta-alvo por QUADRANTE — âncora geométrica relativa ao FRAME, com
mapa PRÓPRIO por ângulo de câmera (fundo × lateral).

Motivação (plano 25/06, áudios do Caio 00000305-00000320): com 4 jogadores em quadra,
a âncora só-aparência ("o de azul") é frágil — a IA analisa quem ela acha mais saliente
(o recebedor, ou os dois ao mesmo tempo), não quem o usuário escolheu. A correção
fechada com o Caio: o usuário aponta, ANTES da análise, em qual **quadrante** do QUADRO
o atleta-alvo COMEÇA o ponto. O quadrante carrega o peso da identidade; a cor/aparência
vira só o fio para SEGUIR o mesmo atleta entre frames.

Por que o mapa depende da CÂMERA (spec "quadrante × câmera", áudio + vídeo lateral do
Caio 25/06): o MESMO canto da tela vira NÚMERO diferente conforme o ângulo, porque a
numeração segue os TIMES e o eixo que separa os times muda com a câmera —

* Câmera de FUNDO — a rede aparece HORIZONTAL no quadro; os times ficam em CIMA (fundo,
  adversário) e EMBAIXO (frente, seu time). Numeração por LINHAS: 1·2 no fundo, 3·4 na
  frente (ímpar = esquerda, par = direita).
* Câmera LATERAL — a rede aparece VERTICAL no quadro; os times ficam à ESQUERDA e à
  DIREITA. Numeração por COLUNAS: 1·2 = time da esquerda, 3·4 = time da direita (em
  cima = fundo, embaixo = perto da câmera).

Consequência cravada pelo Caio: o canto INFERIOR-ESQUERDO da tela é **Q3 visto de
fundo, mas Q2 visto da lateral**. O dedo toca o mesmo lugar; só o sistema sabe traduzir
no atleta certo se souber DE QUAL CÂMERA veio o vídeo — por isso a câmera vem PRIMEIRO,
e tanto o texto do prompt (:func:`build_quadrant_block`) quanto o auto-check de setor
(:func:`quadrant_frame_side`) são parametrizados por ela. É o "parâmetro do prompt que
muda dinamicamente com a seleção do front".

A referência continua sendo o QUADRO do vídeo (zonas superior/inferior × esquerda/
direita da IMAGEM), nunca coordenadas de quadra. NENHUM campo vira ``response_schema``
— é só instrução textual, então o eixo não arrisca derrubar a análise estrita
(invariante 1). Recalibrar a numeração = editar :data:`QUADRANT_MAPS` (mesma filosofia
de "recalibrar = editar a tabela" dos pesos e das regras).
"""

from __future__ import annotations

from typing import TypedDict


class Quadrant(TypedDict):
    label: str          # nome humano (ex.: "frente-esquerda" / "esquerda-perto")
    frame_corner: str   # canto do QUADRO p/ o prompt (ex.: "canto inferior-esquerdo")
    frame_side: str     # lado da IMAGEM ("esquerda" | "direita") p/ o auto-check
    reading: str        # leitura na quadra p/ o laudo (ex.: "fundo · esquerda")


# Só a LATERAL tem mapa próprio (rede vertical, times esq/dir). Fundo, central, atrás
# e QUALQUER outro/desconhecido caem em 'fundo' (rede horizontal, times cima/baixo) — o
# default seguro, que casa com o vídeo de fundo e nunca derruba o roteamento.
_LATERAL_ALIASES = {"lateral", "lado", "side", "lateralizado", "lateralizada"}


def normalize_camera_axis(value: object) -> str:
    """Reduz a referência de câmera ao EIXO do mapa de quadrantes: 'lateral' ou 'fundo'.

    Tolerante (espelha :func:`normalize_quadrant`): qualquer coisa que não seja lateral
    (fundo, central, atrás, vazio, ``None``, lixo) vira ``'fundo'`` — o eixo default.
    """
    s = (str(value) if value is not None else "").strip().lower()
    return "lateral" if s in _LATERAL_ALIASES else "fundo"


# Fonte de verdade da numeração — UM MAPA POR CÂMERA. Grade 2×2 sobre o FRAME:
#   FUNDO   (rede ⎯ horizontal):   1 2   ·  1·2 no fundo (adversário) / 3·4 na frente
#                                   3 4
#   LATERAL (rede ⏐ vertical):     1 3   ·  1·2 = time da esquerda / 3·4 = time da direita
#                                   2 4      (em cima = fundo · embaixo = perto da câmera)
# Cravado na tabela "quadrante × câmera" do Caio (25/06): o inferior-esquerdo é Q3 de
# fundo, Q2 na lateral — mesmo canto, número diferente.
QUADRANT_MAPS: dict[str, dict[int, Quadrant]] = {
    "fundo": {
        1: {"label": "fundo-esquerda",  "frame_corner": "canto superior-esquerdo", "frame_side": "esquerda", "reading": "fundo · esquerda (adversário)"},
        2: {"label": "fundo-direita",   "frame_corner": "canto superior-direito",  "frame_side": "direita",  "reading": "fundo · direita (adversário)"},
        3: {"label": "frente-esquerda", "frame_corner": "canto inferior-esquerdo", "frame_side": "esquerda", "reading": "frente · esquerda (seu time)"},
        4: {"label": "frente-direita",  "frame_corner": "canto inferior-direito",  "frame_side": "direita",  "reading": "frente · direita (seu time)"},
    },
    "lateral": {
        1: {"label": "esquerda-fundo", "frame_corner": "canto superior-esquerdo", "frame_side": "esquerda", "reading": "time da esquerda · ao fundo"},
        2: {"label": "esquerda-perto", "frame_corner": "canto inferior-esquerdo", "frame_side": "esquerda", "reading": "time da esquerda · perto da câmera"},
        3: {"label": "direita-fundo",  "frame_corner": "canto superior-direito",  "frame_side": "direita",  "reading": "time da direita · ao fundo"},
        4: {"label": "direita-perto",  "frame_corner": "canto inferior-direito",  "frame_side": "direita",  "reading": "time da direita · perto da câmera"},
    },
}

VALID_QUADRANTS = tuple(QUADRANT_MAPS["fundo"])  # (1, 2, 3, 4)

# Explicação do EIXO da câmera, injetada no prompt: é o que o agente usa para ler
# 'esquerda/direita' na orientação certa. Muda dinamicamente com a seleção do front.
_AXIS_PROMPT = {
    "fundo": (
        "A câmera filma de FUNDO (atrás da linha de fundo): a REDE corta o QUADRO na "
        "HORIZONTAL — o fundo da quadra (adversários) aparece em CIMA da imagem e a "
        "frente (seu time) EMBAIXO. 'Esquerda' e 'direita' são os lados DA IMAGEM."
    ),
    "lateral": (
        "A câmera filma da LATERAL: a REDE corta o QUADRO na VERTICAL — um time fica à "
        "ESQUERDA e o outro à DIREITA da imagem (quanto mais ao fundo da quadra, mais "
        "para CIMA no quadro). 'Esquerda' e 'direita' são os lados DA IMAGEM."
    ),
}


def normalize_quadrant(value: object) -> int | None:
    """Aceita 3, "3", "Q3", " q3 "… → int 1-4; None (tolerante) p/ vazio/inválido.

    Diferente de :func:`app.tennis.routing.normalize_gender`, NÃO levanta: o
    quadrante é OPCIONAL (vídeo sem quadrante roda como antes). Um valor estranho
    simplesmente desliga a âncora geométrica em vez de derrubar a requisição.
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if s.startswith("q"):
        s = s[1:].strip()
    if not s:
        return None
    try:
        q = int(s)
    except ValueError:
        return None
    return q if q in QUADRANT_MAPS["fundo"] else None


def quadrant_info(quadrant: int | None, camera: object = None) -> Quadrant | None:
    """Linha da tabela para o par (quadrante, câmera). None se o quadrante for inválido.

    A câmera escolhe o MAPA (:func:`normalize_camera_axis`); o default (None/fundo) dá a
    leitura de fundo — exatamente o comportamento de antes, p/ retrocompat dos chamadores
    que não passam câmera.
    """
    q = normalize_quadrant(quadrant)
    if q is None:
        return None
    return QUADRANT_MAPS[normalize_camera_axis(camera)][q]


def quadrant_frame_side(quadrant: int | None, camera: object = None) -> str | None:
    """Lado da IMAGEM do quadrante ("esquerda"/"direita") NA CÂMERA dada — base do auto-check.

    Camera-dependente: Q2 é 'direita' de fundo, mas 'esquerda' na lateral (mesmo número,
    lado diferente). Sem câmera → eixo fundo (retrocompat)."""
    info = quadrant_info(quadrant, camera)
    return info["frame_side"] if info else None


def quadrant_label(quadrant: int | None, camera: object = None) -> str | None:
    """Rótulo humano do quadrante NA CÂMERA dada (ex.: "frente-esquerda" / "esquerda-perto")."""
    info = quadrant_info(quadrant, camera)
    return info["label"] if info else None


def build_quadrant_block(
    quadrant: int | None, appearance: str | None = None, camera: object = None
) -> str | None:
    """Âncora DURA do atleta-alvo por quadrante p/ o system prompt (espelha build_camera_block).

    Substitui a âncora só-aparência como seletor PRIMÁRIO: o atleta é o que COMEÇA o
    ponto no canto do quadro escolhido; a aparência (cor) entra só como FIO de
    continuidade para seguir a MESMA pessoa. O bloco é parametrizado pela ``camera``: o
    canto, a leitura e o ``observed_side`` esperado vêm do mapa daquela câmera
    (:data:`QUADRANT_MAPS`), e o eixo da rede (horizontal/vertical) é explicado para o
    modelo orientar 'esquerda/direita'. Instrui o modelo a (a) seguir só o alvo do início
    ao fim, (b) ignorar os outros 3 e a quadra adjacente, (c) NÃO trocar de jogador se
    ninguém começar ali (baixar a confiança em vez de "preencher"), e (d) reportar
    'positioning.observed_side' do alvo em termos DA IMAGEM, para o auto-check de setor
    (:func:`app.tennis.service._check_target_sector`) conferir que travou no atleta certo.
    Retorna ``None`` se não houver quadrante (degrada p/ o fluxo só-aparência de antes).
    """
    q = normalize_quadrant(quadrant)
    if q is None:
        return None
    axis = normalize_camera_axis(camera)
    info = QUADRANT_MAPS[axis][q]
    look = (appearance or "").strip()
    appearance_line = (
        f"- O alvo veste {look}. Use a aparência APENAS como fio para SEGUIR o mesmo "
        "atleta entre os quadros — NÃO para escolhê-lo (quem escolhe é o quadrante).\n"
        if look else ""
    )
    return (
        "ÂNCORA GEOMÉTRICA DO ATLETA-ALVO (definida pelo usuário ANTES da análise — "
        "tem PRECEDÊNCIA sobre a aparência e sobre qualquer saliência visual):\n"
        f"- {_AXIS_PROMPT[axis]}\n"
        f"- O atleta a analisar COMEÇA O PONTO no {info['frame_corner']} da IMAGEM "
        f"(quadrante {q} — {info['reading']}).\n"
        f"{appearance_line}"
        "- A referência é o QUADRO do vídeo, não a quadra real: o canto é o que aparece "
        "na imagem (a numeração já vem ajustada para esta câmera).\n"
        "- Trave nesse atleta no PRIMEIRO quadro e SIGA SOMENTE ELE do início ao fim do "
        "ponto, mesmo que ele se desloque para outro setor da quadra. Não troque de "
        "pessoa no meio do rally.\n"
        "- IGNORE os outros 3 jogadores (o parceiro e os dois adversários) e QUALQUER "
        "pessoa de uma quadra ao lado/adjacente que apareça no enquadramento.\n"
        "- Se NÃO houver atleta começando o ponto nesse canto (ex.: o quadrante está "
        "vazio no primeiro quadro), DIGA isso em 'visual_evidence' e baixe "
        "'subject_lock_confidence' para 'baixa' — NUNCA troque por outro jogador só "
        "para preencher a análise.\n"
        "- Ao preencher 'positioning.observed_side' do alvo, reporte o lado DA IMAGEM "
        "no INÍCIO do ponto — MESMO que ele se desloque para o outro lado depois, este "
        f"campo é o lado do QUADRO onde ele COMEÇOU (esperado: '{info['frame_side']}'; "
        "use 'esquerda'/'centro'/'direita' do QUADRO). É isso que confirma que a análise "
        "travou no atleta certo."
    )
