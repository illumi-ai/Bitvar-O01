"""Schemas Pydantic do tênis — fonte única de verdade.

São usados em dois papéis (blueprint §03/§04/§05):

1. como ``response_schema`` da chamada 1 ao Gemini (vídeo → JSON estruturado);
2. para validar/normalizar o JSON que volta antes de alimentar as três saídas.

Decisões de fidelidade ao blueprint:

* O blueprint fala em "4 schemas" (male·clip, male·match, fem·clip, fem·match).
  Estruturalmente há **2** formatos (``ClipAnalysis`` e ``MatchAnalysis``): o que
  muda por gênero são o ``gender_profile``, o ``benchmark_reference`` e o modelo de
  pesos — selecionados no roteamento (:mod:`app.tennis.routing`). São, na prática,
  4 *configurações* enxutas, evitando o mega-schema com ``anyOf`` que a doc
  desaconselha.
* ``weighted_performance_score`` **não** é pedido ao modelo: VLMs erram aritmética.
  Ele é calculado deterministicamente em :mod:`app.tennis.weights` a partir das
  estatísticas brutas, o que torna a calibração de pesos (Fase 5) uma mudança de
  código, não de prompt.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Gender = Literal["male", "female"]
Mode = Literal["clip", "match"]
# Eixo de NÍVEL/categoria (amador|profissional) — parâmetro de ENTRADA, igual a
# Gender/Mode (calibragem Caio 24/06, eixo WF2). NÃO é campo de response_schema:
# entra pelo form e é exposto em RouteInfo para transparência. Usado por
# app.tennis.rules para decidir quais regras táticas são cobráveis.
Level = Literal["amador", "profissional"]

ShotType = Literal[
    "forehand", "backhand", "serve", "return", "volley",
    "overhead_smash", "slice", "drop_shot", "lob", "approach", "unknown",
]

# Eixo de FASE/PAPEL do atleta no lance — distinto do GOLPE (``ShotType``).
# É a peça que faltava no schema: o protótipo era forçado a rotular um golpe
# ofensivo (ex.: smash) mesmo quando o atleta estava em recepção/defesa. Ter a
# fase separada deixa o modelo classificar o papel ANTES de avaliar o golpe.
ActionPhase = Literal[
    "serve",          # sacando
    "serve_return",   # recebendo o saque (recepção)
    "baseline_rally", # troca de fundo de quadra
    "attack",         # atacando (ex.: parceiro saca, ele finaliza)
    "defense",        # defendendo / em desvantagem no ponto
    "transition",     # subindo à rede / deslocamento
    "net_play",       # jogo de rede (voleio/finalização)
    "unknown",
]

# Confiança auto-declarada pelo modelo — torna a incerteza visível (auditável)
# em vez de silenciosa. Usado para a fase, o golpe e o "travamento" no atleta.
Confidence = Literal["baixa", "media", "alta"]

# Zonas qualitativas e relativas (NÃO coordenadas): o VLM raciocina espaço
# grosseiro a partir de uma câmera, mas não dá medida métrica sem homografia.
CourtZone = Literal["fundo", "meio", "rede", "transicao"]
LateralPosition = Literal["esquerda", "centro", "direita"]
Handedness = Literal["destro", "canhoto", "indeterminado"]

# Catálogo FECHADO de eventos táticos relacionais que o treinador lê e o protótipo
# ignora (calibragem Caio 24/06, eixo WF4). O modelo hoje parte do GOLPE do alvo e
# perde a LEITURA DO PONTO: a finta do adversário, o vazio que se abre, o
# aproveitamento do deslocamento da dupla. Enumerar fecha o vocabulário e evita
# rótulos soltos. "outro" é a válvula de escape honesta (sem inventar categoria).
TacticalEventType = Literal[
    "finta",                        # amago/dissimulação para enganar o adversário
    "aproveitamento_deslocamento",  # explora a dupla adversária fora de posição
    "espaco_livre",                 # finaliza no vazio aberto na quadra adversária
    "colocacao",                    # bola colocada com intenção (ângulo/cantinho)
    "quebra_de_ritmo",              # muda o tempo do ponto (deixadinha, bola lenta)
    "outro",
]

# QUEM protagoniza o evento — leitura relacional dos 4 jogadores do ponto (no beach
# tennis em duplas: alvo + parceiro × dois adversários). "indefinido" quando a
# perspectiva da câmera não permite atribuir com confiança.
TacticalActor = Literal["alvo", "adversario", "parceiro", "indefinido"]


# --------------------------------------------------------------------------- #
# bloco compartilhado                                                          #
# --------------------------------------------------------------------------- #
class ScoreObs(BaseModel):
    """Par {nota 0-10, observação} — o shape repetido em todo o bloco técnico."""

    score: int = Field(ge=0, le=10, description="Nota inteira de 0 a 10.")
    observation: str = Field(description="Justificativa técnica curta da nota, em PT-BR.")


# --------------------------------------------------------------------------- #
# CLIP — análise técnica do lance (estrutura idêntica M/F)                     #
# --------------------------------------------------------------------------- #
class TechnicalExecution(BaseModel):
    preparation: ScoreObs
    contact_point: ScoreObs
    follow_through: ScoreObs
    balance_and_posture: ScoreObs
    racket_path: ScoreObs


class FootworkMovement(BaseModel):
    split_step: ScoreObs
    court_positioning: ScoreObs
    recovery_after_shot: ScoreObs


class Biomechanics(BaseModel):
    kinetic_chain: ScoreObs
    hip_shoulder_rotation: ScoreObs
    weight_transfer: ScoreObs


class LowerBodyBase(BaseModel):
    """A 'raiz do movimento' — MEMBROS INFERIORES / BASE (eixo WF3, Caio 24/06).

    O especialista lê a qualidade do lance pela BASE (joelhos flexionados, centro
    de gravidade baixo, estabilidade), não pelo braço — é a raiz técnica real do
    erro do 00000201. Estes campos tornam a flexão/estabilidade avaliáveis e
    ponderáveis (:mod:`app.tennis.weights`); ``floating_ball_fault`` (em
    ``ClipAnalysis``) é o detector explícito do padrão 'pernas altas/estendidas +
    raquete baixa => bola que flutua'. Todos os campos são opcionais: o modelo só
    preenche o que viu, e o score re-normaliza sobre os presentes.
    """

    defensive_base_flexion: ScoreObs | None = Field(
        default=None,
        description="Flexão de joelhos/quadril na RECEPÇÃO ou DEFESA — base baixa e estável recebe melhor (10 = base baixa ideal).",
    )
    movement_base_flexion: ScoreObs | None = Field(
        default=None,
        description="Flexão da base no DESLOCAMENTO até a bola (split step com joelhos flexionados, não pernas retas).",
    )
    stability_center_of_gravity: ScoreObs | None = Field(
        default=None,
        description="Estabilidade do centro de gravidade no contato (base firme x desequilíbrio por pernas altas).",
    )


class TacticalEvent(BaseModel):
    """Um evento tático RELACIONAL do ponto (eixo WF4, calibragem Caio 24/06).

    Distinto de :class:`TacticalIntent`, que avalia a INTENÇÃO do golpe do alvo.
    Aqui o foco é o PONTO entre os 4 jogadores: a finta do adversário, o vazio
    que se abre, o aproveitamento do deslocamento da dupla. Todos os campos são
    descritivos/qualitativos — nenhuma nota e nenhuma coordenada (a câmera de
    quadra não dá medida métrica). É item de lista dentro de ``ClipAnalysis``:
    ``event_type``/``description`` são obrigatórios DENTRO do item (o item só
    existe se o modelo decidir emitir um evento), mas a LISTA é Optional/None, então
    o schema estrito não quebra se o modelo não enxergar nada relacional (invariante 1).
    """

    event_type: TacticalEventType = Field(
        description="Categoria do evento tático (catálogo fechado)."
    )
    description: str = Field(
        description="O que aconteceu, em PT-BR e em termos relacionais "
        "(quem fez, contra quem, que espaço abriu). Curto e concreto."
    )
    approx_timestamp_s: float | None = Field(
        default=None, ge=0,
        description="Instante aproximado do evento em segundos (estimativa em fps baixo).",
    )
    actor: TacticalActor | None = Field(
        default=None,
        description="Quem protagonizou o evento (alvo/adversario/parceiro/indefinido).",
    )


class TacticalIntent(BaseModel):
    shot_placement_quality: ScoreObs
    shot_selection: ScoreObs


class PositioningRead(BaseModel):
    """Leitura QUALITATIVA de posicionamento (A4 + C3, feedback Caio 13/06).

    Onde o atleta ESTAVA e onde DEVERIA estar naquele contexto, em zonas
    relativas e grosseiras (fundo/meio/rede × esquerda/centro/direita). Nunca
    coordenadas exatas — uma câmera sem calibração não dá medida métrica.
    """

    observed_zone: CourtZone | None = Field(default=None, description="Zona onde o atleta estava.")
    observed_side: LateralPosition | None = Field(default=None, description="Lado relativo observado.")
    recommended_zone: CourtZone | None = Field(default=None, description="Zona ideal naquele contexto.")
    recommended_side: LateralPosition | None = Field(default=None, description="Lado ideal naquele contexto.")
    rationale: str | None = Field(default=None, description="Por que essa posição é melhor — em PT-BR.")


class ClipAnalysis(BaseModel):
    """Schema do modo CLIP (blueprint §4.1).

    Os campos do feedback do Caio (fase, confiança, evidência, posicionamento,
    travamento no atleta) são **todos opcionais**: ``ClipAnalysis`` é o
    ``response_schema`` estrito do Gemini (sem fallback de parsing), então um
    campo obrigatório novo arriscaria derrubar a análise inteira se o modelo
    omitisse. Opcional com default ``None`` mantém a compatibilidade.
    """

    analysis_mode: Literal["clip"]
    gender_profile: Gender
    shot_identified: ShotType = Field(description="Golpe principal identificado no clipe.")

    # --- eixo de FASE/PAPEL + auditabilidade (C1 / D1, feedback Caio 13/06) ---
    action_phase: ActionPhase | None = Field(
        default=None,
        description="O que o atleta está FAZENDO (saque/recepção/ataque/defesa…) — classifique ANTES do golpe.",
    )
    phase_confidence: Confidence | None = Field(default=None, description="Confiança na fase identificada.")
    shot_confidence: Confidence | None = Field(default=None, description="Confiança no golpe identificado.")
    # --- fase concorrente / auditabilidade (eixo WF1, calibragem Caio 24/06) ---
    phase_alternative: ActionPhase | None = Field(
        default=None,
        description=(
            "Hipótese de FASE concorrente quando o lance é ambíguo (ex.: parece "
            "ataque mas pode ser defesa). Preencha SÓ quando houver dúvida real e, "
            "nesse caso, rebaixe 'phase_confidence'. Deixe nulo se a fase for clara."
        ),
    )
    phase_alternative_rationale: str | None = Field(
        default=None,
        description=(
            "Por que a fase é ambígua e o que distinguiria uma da outra (em PT-BR). "
            "Ex.: 'a bola vem do saque adversário e o atleta recua, então pode ser "
            "recepção/defesa e não ataque'."
        ),
    )
    approx_timestamp_s: float | None = Field(
        default=None, ge=0, description="Instante aproximado do lance em segundos (estimativa em fps baixo)."
    )
    visual_evidence: str | None = Field(
        default=None, description="O que de fato foi VISTO no vídeo que embasa a classificação (auditável)."
    )
    subject_lock_confidence: Confidence | None = Field(
        default=None, description="Quão certo o modelo está de ter analisado a pessoa-alvo correta.",
    )
    handedness: Handedness | None = Field(
        default=None, description="Lateralidade observada (pela mão que segura a raquete)."
    )
    positioning: PositioningRead | None = Field(
        default=None, description="Leitura qualitativa de posição/setorização (onde estava × onde deveria)."
    )

    technical_execution: TechnicalExecution
    footwork_and_movement: FootworkMovement | None = None
    biomechanics: Biomechanics | None = None
    tactical_intent: TacticalIntent | None = None

    # --- MEMBROS INFERIORES / BASE + bola flutuante (eixo WF3, Caio 24/06) ---
    lower_body_base: LowerBodyBase | None = Field(
        default=None,
        description="Avaliação dos MEMBROS INFERIORES / BASE (flexão de joelhos, estabilidade). Base da nota em fases defensivas.",
    )
    floating_ball_fault: bool | None = Field(
        default=None,
        description="True se o padrão 'pernas estendidas/altas + raquete baixa => bola que flutua (sem controle)' estiver presente.",
    )
    floating_ball_observation: str | None = Field(
        default=None,
        description="Descrição em PT-BR do que evidencia (ou não) a bola flutuante — base, altura da raquete e trajetória.",
    )

    # --- LEITURA TÁTICA DO PONTO (eixo WF4, calibragem Caio 24/06) ---
    # Eventos relacionais entre os 4 jogadores que o treinador vê e o protótipo
    # ignora. Opcional/None e com teto de itens para não estourar o JSON nem o
    # custo da chamada. Default None mantém o schema estrito retro-compatível.
    tactical_events: list[TacticalEvent] | None = Field(
        default=None,
        max_length=5,
        description="Até 5 eventos táticos relacionais do ponto (finta, "
        "aproveitamento de deslocamento, espaço livre, colocação, quebra de ritmo).",
    )
    point_outcome_link: str | None = Field(
        default=None,
        description="Encadeamento causa→efeito ligando a tática ao resultado do "
        "ponto, em PT-BR (ex.: 'não caiu na finta, aproveitou o vazio e finalizou').",
    )

    clip_quality_score: float = Field(
        ge=0, le=10, description="Nota técnica ponderada do lance (0-10)."
    )
    key_improvement: str = Field(
        description="A principal correção acionável. ENTREGA CENTRAL do produto."
    )
    secondary_improvements: list[str] | None = Field(
        default=None, max_length=3, description="Até 3 correções secundárias."
    )


# --------------------------------------------------------------------------- #
# MATCH — estatística da partida (mesmos campos M/F; muda o modelo de pesos)   #
# --------------------------------------------------------------------------- #
class ServeStats(BaseModel):
    first_serve_in_pct: float | None = Field(default=None, description="% de 1os saques dentro.")
    first_serve_points_won_pct: float | None = Field(default=None, description="% pontos ganhos no 1o saque.")
    second_serve_points_won_pct: float | None = Field(default=None, description="% pontos ganhos no 2o saque.")
    aces: int | None = None
    double_faults: int | None = None
    serve_dominance_observation: str | None = None
    benchmark_reference: str | None = Field(default=None, description="Norma ATP/WTA de referência.")


class ReturnStats(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    return_points_won_pct: float | None = None
    return_games_won: int | None = None
    benchmark_reference: str | None = None


class RallyStats(BaseModel):
    avg_rally_length: float | None = Field(default=None, description="Média de golpes por ponto.")
    rally_0_4_pct: float | None = None
    rally_5_8_pct: float | None = None
    rally_9_plus_pct: float | None = None
    baseline_points_won_pct: float | None = None
    net_points_won_pct: float | None = None
    benchmark_reference: str | None = None


class OutcomeQuality(BaseModel):
    winners: int | None = None
    unforced_errors: int | None = None
    forced_errors: int | None = None
    winner_to_ue_ratio: float | None = Field(
        default=None, description="Razão winners/erros não forçados — maior discriminador."
    )
    observation: str | None = None


class PressurePoints(BaseModel):
    break_points_faced: int | None = None
    break_points_saved: int | None = None
    break_points_converted: int | None = None
    break_points_opportunities: int | None = None
    observation: str | None = None


class MatchAnalysis(BaseModel):
    """Schema do modo MATCH (blueprint §4.2). Sem ``weighted_performance_score``:
    ele é computado em :mod:`app.tennis.weights` a partir destes campos brutos."""

    model_config = ConfigDict(populate_by_name=True)

    analysis_mode: Literal["match"]
    gender_profile: Gender

    serve: ServeStats
    return_: ReturnStats = Field(alias="return")
    rally: RallyStats
    outcome_quality: OutcomeQuality
    pressure_points: PressurePoints

    key_improvement: str = Field(description="A principal correção acionável da partida.")
    secondary_improvements: list[str] | None = Field(default=None, max_length=3)


# --------------------------------------------------------------------------- #
# modelos de E/S da API                                                        #
# --------------------------------------------------------------------------- #
class RouteInfo(BaseModel):
    """Como o vídeo foi roteado — exposto p/ transparência (blueprint §02)."""

    gender: Gender
    mode: Mode
    level: Level = "amador"  # categoria de cobrança de regras (amador|profissional)
    fps: int
    media_resolution: str
    thinking_level: str
    schema_name: str
    weight_model: str | None = None
    duration_seconds: float | None = None
    mode_detection: str = Field(description="Como o modo foi decidido (duração/override/heurística).")


class SubjectHint(BaseModel):
    """Identificação do jogador a analisar (o take pode ter várias pessoas).

    São **atributos de aparência**, não biometria: ajudam o VLM a isolar o
    alvo por descrição (roupa, lateralidade, boné, raquete), sem reconhecimento
    facial nem identificação nominal por rosto — que a política da Google
    proíbe e o vídeo de quadra não comporta tecnicamente (feedback Caio 13/06,
    spec A2 inviável → ancoragem por aparência). Altura/peso ficam de fora de
    propósito: discriminadores fracos para o VLM e dado pessoal sensível (LGPD).
    """

    name: str | None = None
    outfit: str | None = Field(default=None, description="Roupa/aparência (ex.: camiseta azul).")
    side: str | None = Field(default=None, description="Lado/posição na quadra.")
    notes: str | None = None
    # atributos discriminantes não-biométricos (spec A1)
    handedness: Handedness | None = Field(default=None, description="Destro/canhoto — âncora robusta.")
    headwear: str | None = Field(default=None, description="Boné/viseira e cor (ex.: boné branco).")
    racket_color: str | None = Field(default=None, description="Cor/marca da raquete.")
    glasses: bool | None = Field(default=None, description="Usa óculos?")
    hair: str | None = Field(default=None, description="Cabelo (cor/comprimento).")

    _IDENTIFYING = ("name", "outfit", "side", "notes", "handedness", "headwear", "racket_color", "hair")

    def provided(self) -> bool:
        if self.glasses is True:
            return True
        return any(v and str(v).strip() for v in (getattr(self, f) for f in self._IDENTIFYING))


class TennisAnalysisResponse(BaseModel):
    """Payload das três saídas (blueprint §07): métricas, texto e áudio."""

    ok: bool = True
    route: RouteInfo
    subject: SubjectHint | None = Field(default=None, description="Identificação do jogador usada.")
    metrics: dict = Field(description="JSON da chamada 1 (+ weighted_performance_score no match).")
    benchmarks: dict = Field(default_factory=dict, description="Normas numéricas p/ barras de comparação.")
    narrative: str | None = Field(default=None, description="Texto de coach PT-BR (chamada 2).")
    audio_base64: str | None = Field(default=None, description="WAV base64 da narrativa (chamada 3).")
    audio_mime: str = "audio/wav"
    warnings: list[str] = Field(default_factory=list)
    persisted_id: int | None = None
