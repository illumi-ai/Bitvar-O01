"""Contratos estruturados da vertical Academia.

O envelope identifica exercícios de musculação de forma genérica e só executa
uma metodologia técnica quando existe um perfil local compatível. O perfil
inicial descreve a análise visual de agachamento e separa três coisas que não
podem ser confundidas:

* qualidade da captura (é possível avaliar?);
* observações por repetição e por critério;
* score agregado, calculado deterministicamente em :mod:`app.academia.scoring`.

A metodologia é uma POC não validada. Os modelos, portanto, tornam limitações e
itens não observáveis explícitos em vez de induzir o VLM a preencher lacunas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# O registro de perfis valida os slugs. Manter ``str`` aqui permite acrescentar
# um novo schema/perfil sem reescrever o envelope nem o motor do serviço (RF-012).
ExerciseSlug = str
CaptureAngle = Literal["frontal", "lateral", "posterior", "diagonal", "unknown"]
Confidence = Literal["baixa", "media", "alta"]
CaptureStatus = Literal["adequate", "limited", "inadequate"]
AnalysisStatus = Literal[
    "complete",
    "limited",
    "recapture_required",
    "unsupported_exercise",
    "exercise_unknown",
]
CriterionVerdict = Literal[
    "adequado",
    "ajuste_leve",
    "a_corrigir",
    "nao_avaliavel",
    "nao_aplicavel",
]
ApproximateCriterionState = Literal[
    "adequado",
    "ajuste_leve",
    "a_corrigir",
    "nao_observavel",
]
SquatCriterionId = Literal[
    "stance_and_foot_position",
    "foot_contact",
    "knee_tracking",
    "squat_depth",
    "trunk_control",
    "hip_knee_coordination",
    "tempo_control",
    "bilateral_symmetry",
]
GeneralCriterionId = Literal[
    "range_pattern",
    "tempo_pattern",
    "trajectory_pattern",
    "stability_pattern",
    "alignment_pattern",
    "equipment_pattern",
    "repetition_consistency_pattern",
    "transition_pattern",
]
MethodologyStatus = Literal["poc_unvalidated", "expert_validated"]
MethodologyScope = Literal["exercise_specific", "general_execution", "none"]
MovementPhase = Literal["inicio", "descida", "fundo", "subida", "fim"]
MovementConsistency = Literal["consistente", "variavel", "inconclusivo"]
GeneralTempoStyle = Literal[
    "lento_controlado",
    "moderado_controlado",
    "rapido_controlado",
    "rapido_sem_controle",
    "irregular",
    "nao_avaliavel",
]
GeneralExecutionClassification = Literal[
    "adequada_ao_padrao_observado",
    "parcialmente_adequada",
    "necessita_ajustes",
    "nao_avaliavel",
]
IdentificationStatus = Literal["identified", "unknown", "mixed", "no_exercise"]
ExerciseFamily = Literal[
    "squat",
    "machine_squat",
    "leg_press",
    "hinge",
    "lunge",
    "horizontal_press",
    "vertical_press",
    "horizontal_pull",
    "vertical_pull",
    "knee_extension",
    "knee_flexion",
    "elbow_flexion",
    "elbow_extension",
    "calf_raise",
    "core",
    "other",
    "unknown",
]
EquipmentCategory = Literal[
    "bodyweight",
    "barbell",
    "dumbbell",
    "kettlebell",
    "smith_machine",
    "cable_machine",
    "selectorized_machine",
    "plate_loaded_machine",
    "other",
    "unknown",
]
TargetStatus = Literal["tracked", "ambiguous", "not_found"]
IdentificationReason = Literal[
    "supported",
    "general_supported",
    "unsupported",
    "low_confidence",
    "mixed",
    "no_exercise",
    "target_ambiguous",
    "unknown",
]
NonNegativeTimestamp = Annotated[float, Field(ge=0)]
PositiveRepetitionIndex = Annotated[int, Field(ge=1)]


class PractitionerHint(BaseModel):
    """Identificação opcional fornecida pelo usuário, nunca inferida do rosto."""

    id: str | None = Field(
        default=None,
        max_length=120,
        description="Identificador opaco opcional para evolução futura.",
    )
    name: str | None = Field(
        default=None,
        max_length=120,
        description="Nome/rótulo opcional para personalizar o relatório.",
    )
    outfit: str | None = Field(
        default=None,
        max_length=240,
        description="Aparência/roupa usada apenas para seguir a mesma pessoa.",
    )
    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Contexto visual opcional; não deve conter instruções clínicas.",
    )

    def provided(self) -> bool:
        return any(
            value and str(value).strip()
            for value in (self.id, self.name, self.outfit, self.notes)
        )


class CaptureQuality(BaseModel):
    """Gate visual que precede qualquer conclusão sobre a execução."""

    status: CaptureStatus
    confidence: Confidence
    detected_camera_angle: CaptureAngle = "unknown"
    exercise_visible: bool | None = Field(
        default=None,
        description="Há um agachamento observável, não apenas outra atividade.",
    )
    whole_body_visible: bool | None = Field(
        default=None,
        description="Cabeça, tronco, quadril, joelhos e pés permanecem visíveis.",
    )
    feet_visible: bool | None = None
    target_person_trackable: bool | None = Field(
        default=None,
        description=(
            "A pessoa-alvo permaneceu distinguível e pôde ser seguida durante o exercício."
        ),
    )
    other_people_visible: bool | None = Field(
        default=None,
        description="Há outras pessoas no quadro, sem implicar ambiguidade por si só.",
    )
    single_person_visible: bool | None = None
    stable_camera: bool | None = None
    adequate_lighting: bool | None = None
    issues: list[str] = Field(default_factory=list, max_length=8)
    recapture_instructions: list[str] = Field(default_factory=list, max_length=6)


class MovementPhaseTimestamp(BaseModel):
    """Marco temporal aproximado de uma fase dentro de uma repetição."""

    phase: MovementPhase
    timestamp_s: float = Field(ge=0)
    observable: bool = True


class RepetitionSegment(BaseModel):
    """Segmentação lógica de uma repetição; não afirma recorte físico do vídeo."""

    index: int = Field(ge=1)
    complete: bool
    start_s: float | None = Field(default=None, ge=0)
    bottom_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    phases: list[MovementPhaseTimestamp] = Field(default_factory=list, max_length=5)
    confidence: Confidence
    observation: str | None = None


class MovementSummary(BaseModel):
    """Resumo consolidado do padrão cíclico observado no vídeo."""

    exercise_detected: bool
    detected_repetitions: int = Field(ge=0)
    complete_repetitions: int = Field(ge=0)
    confidence: Confidence
    range_consistency: str | None = None
    tempo_consistency: str | None = None
    overall_observation: str


class CriterionAssessment(BaseModel):
    """Veredito auditável para um item da metodologia versionada."""

    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=160)
    verdict: CriterionVerdict
    score: float | None = Field(
        default=None,
        ge=0,
        le=10,
        description=(
            "Nota observacional do critério. Deve ser nula quando o veredito for "
            "nao_avaliavel; a agregação é feita em Python."
        ),
    )
    confidence: Confidence
    observation: str
    correction: str | None = None
    coaching_suggestion: str | None = Field(
        default=None,
        max_length=600,
        description=(
            "Orientação prática de manutenção ou refinamento. Pode existir mesmo "
            "quando o critério está adequado e não representa diagnóstico."
        ),
    )
    muscle_context: str | None = Field(
        default=None,
        max_length=600,
        description=(
            "Papel muscular esperado segundo a metodologia local; não representa "
            "medição de ativação por vídeo."
        ),
    )
    evidence_timestamps_s: list[NonNegativeTimestamp] = Field(
        default_factory=list, max_length=8
    )
    affected_repetitions: list[PositiveRepetitionIndex] = Field(
        default_factory=list, max_length=12
    )


# Schemas de transporte do VLM. O contrato HTTP completo é deliberadamente
# maior, mas o Gemini rejeita schemas muito largos/profundamente aninhados. A
# análise visual é dividida em dois passes estritos e depois materializada em
# ``SquatAnalysis`` antes de qualquer score ou texto publicável.
class VisionCaptureQuality(BaseModel):
    status: CaptureStatus
    confidence: Confidence
    detected_camera_angle: CaptureAngle
    exercise_visible: bool
    whole_body_visible: bool
    feet_visible: bool
    # O fallback para respostas/fakes anteriores usa ``single_person_visible``.
    # Os novos campos são solicitados pelo prompt, mas permanecem opcionais no
    # transporte durante a migração do contrato.
    target_person_trackable: bool | None = None
    other_people_visible: bool | None = None
    # Campo legado: significa que uma única pessoa-alvo pôde ser isolada, ainda
    # que outras pessoas apareçam ao fundo. Novos gates devem preferir
    # ``target_person_trackable``.
    single_person_visible: bool
    stable_camera: bool
    adequate_lighting: bool


class ExerciseIdentificationPass(BaseModel):
    """Passo VLM genérico: identificação, sem avaliação biomecânica."""

    model_config = ConfigDict(extra="forbid")

    status: IdentificationStatus
    exercise_family: ExerciseFamily
    exercise_name_pt_br: str = Field(max_length=80)
    variation_pt_br: str = Field(max_length=80)
    equipment_category: EquipmentCategory
    equipment_name_pt_br: str = Field(max_length=80)
    confidence: Confidence
    target_status: TargetStatus
    multiple_people_visible: bool
    multiple_exercises_visible: bool
    # ``-1`` é o sentinela remoto para intervalo não identificável.
    active_start_s: float = Field(ge=-1, allow_inf_nan=False)
    active_end_s: float = Field(ge=-1, allow_inf_nan=False)


class VisionMovementSummary(BaseModel):
    exercise_detected: bool
    detected_repetitions: int = Field(ge=0)
    complete_repetitions: int = Field(ge=0)
    confidence: Confidence
    range_consistency: MovementConsistency
    tempo_consistency: MovementConsistency


class VisionRepetitionSegment(BaseModel):
    index: int = Field(ge=1)
    complete: bool
    # ``-1`` é o sentinela obrigatório para um marco não observável. Isso evita
    # unions nullable no schema remoto; o serviço converte o valor para None.
    start_s: float = Field(ge=-1, allow_inf_nan=False)
    bottom_s: float = Field(ge=-1, allow_inf_nan=False)
    end_s: float = Field(ge=-1, allow_inf_nan=False)
    confidence: Confidence


class SquatCapturePass(BaseModel):
    """Passo VLM 1A: gate visual, contagem e âncoras temporais."""

    capture_quality: VisionCaptureQuality
    movement: VisionMovementSummary
    repetitions: list[VisionRepetitionSegment] = Field(max_length=12)


class VisionCriterionAssessment(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    verdict: CriterionVerdict
    # Para ``nao_avaliavel`` o modelo usa zero; a materialização força None.
    score: float = Field(ge=0, le=10)
    confidence: Confidence
    evidence_timestamps_s: list[NonNegativeTimestamp] = Field(max_length=8)
    affected_repetitions: list[PositiveRepetitionIndex] = Field(max_length=12)


class SquatChecklistPass(BaseModel):
    """Passe remoto 1B compacto, sem arrays aninhados rejeitados pelo Gemini."""

    model_config = ConfigDict(extra="forbid")

    assessment_confidence: Confidence
    stance_and_foot_position: ApproximateCriterionState
    foot_contact: ApproximateCriterionState
    knee_tracking: ApproximateCriterionState
    squat_depth: ApproximateCriterionState
    trunk_control: ApproximateCriterionState
    hip_knee_coordination: ApproximateCriterionState
    tempo_control: ApproximateCriterionState
    bilateral_symmetry: ApproximateCriterionState
    primary_focus: SquatCriterionId


class GeneralVisionCaptureQuality(BaseModel):
    """Gate remoto aplicável a exercícios livres, cabos e máquinas."""

    model_config = ConfigDict(extra="forbid")

    status: CaptureStatus
    confidence: Confidence
    detected_camera_angle: CaptureAngle
    exercise_visible: bool
    relevant_body_regions_visible: bool
    equipment_visible: bool
    target_person_trackable: bool
    other_people_visible: bool
    stable_camera: bool
    adequate_lighting: bool


class GeneralVisionMovementSummary(BaseModel):
    """Resumo remoto sem tentar declarar eficácia ou adaptação fisiológica."""

    model_config = ConfigDict(extra="forbid")

    exercise_detected: bool
    detected_repetitions: int = Field(ge=0)
    complete_repetitions: int = Field(ge=0)
    confidence: Confidence
    range_consistency: MovementConsistency
    tempo_consistency: MovementConsistency
    trajectory_consistency: MovementConsistency


class GeneralVisionRepetitionSegment(BaseModel):
    """Ciclo genérico: início → mudança principal de direção → fim."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    complete: bool
    start_s: float = Field(ge=-1, allow_inf_nan=False)
    transition_s: float = Field(ge=-1, allow_inf_nan=False)
    end_s: float = Field(ge=-1, allow_inf_nan=False)
    confidence: Confidence


class GeneralExecutionCapturePass(BaseModel):
    """Passe remoto 1A da análise observacional genérica."""

    model_config = ConfigDict(extra="forbid")

    capture_quality: GeneralVisionCaptureQuality
    movement: GeneralVisionMovementSummary
    repetitions: list[GeneralVisionRepetitionSegment] = Field(max_length=16)


GeneralRangePattern = Literal[
    "consistente_controlada",
    "reduzida_consistente",
    "variavel",
    "encurtada_abruptamente",
    "nao_observavel",
]
GeneralTempoPattern = Literal[
    "lento_controlado",
    "moderado_controlado",
    "rapido_controlado",
    "rapido_sem_controle",
    "irregular",
    "nao_observavel",
]
GeneralTrajectoryPattern = Literal[
    "consistente_controlada",
    "desvio_repetido",
    "mudanca_abrupta",
    "nao_observavel",
]
GeneralStabilityPattern = Literal[
    "estavel",
    "oscilacao_leve",
    "perda_repetida",
    "nao_observavel",
]
GeneralAlignmentPattern = Literal[
    "coerente_no_plano_visivel",
    "variacao_repetida",
    "nao_observavel",
]
GeneralEquipmentPattern = Literal[
    "contato_e_ajuste_estaveis",
    "perda_de_contato",
    "ajuste_ou_posicao_instavel",
    "impacto_no_fim_do_curso",
    "nao_observavel",
    "nao_aplicavel",
]
GeneralConsistencyPattern = Literal[
    "repeticoes_padronizadas",
    "variacao_progressiva",
    "muito_irregular",
    "nao_observavel",
]
GeneralTransitionPattern = Literal[
    "transicoes_controladas",
    "uso_de_impulso_ou_rebote",
    "travamento_ou_impacto_abrupto",
    "nao_observavel",
]


class GeneralExecutionChecklistPass(BaseModel):
    """Passe remoto 1B: padrões enumerados, sem prosa clínica ou prescrição."""

    model_config = ConfigDict(extra="forbid")

    assessment_confidence: Confidence
    range_pattern: GeneralRangePattern
    tempo_pattern: GeneralTempoPattern
    trajectory_pattern: GeneralTrajectoryPattern
    stability_pattern: GeneralStabilityPattern
    alignment_pattern: GeneralAlignmentPattern
    equipment_pattern: GeneralEquipmentPattern
    repetition_consistency_pattern: GeneralConsistencyPattern
    transition_pattern: GeneralTransitionPattern
    primary_focus: GeneralCriterionId


class ExecutionScoreComponent(BaseModel):
    criterion_id: str
    label: str
    weight: float = Field(ge=0, le=1)
    effective_weight: float = Field(ge=0, le=1)
    normalized: float | None = Field(default=None, ge=0, le=1)
    contribution_points: float = Field(ge=0, le=100)
    present: bool


class WeightedExecutionScore(BaseModel):
    """Resultado da agregação determinística, nunca preenchido pelo VLM."""

    score: float | None = Field(default=None, ge=0, le=100)
    weighting_model: str
    methodology_version: str
    valid: bool
    criteria_present: int = Field(ge=0)
    criteria_total: int = Field(ge=1)
    coverage: float = Field(ge=0, le=1)
    component_breakdown: list[ExecutionScoreComponent]
    note: str | None = None


class LiteratureReference(BaseModel):
    """Fonte registrada pelo perfil técnico, sem ser gerada pelo VLM."""

    citation: str = Field(min_length=1, max_length=240)
    url: str = Field(min_length=1, max_length=500)


class SquatAnalysis(BaseModel):
    """Contrato público materializado dos dois passes de visão do agachamento."""

    analysis_mode: Literal["exercise"] = "exercise"
    exercise: Literal["squat"] = "squat"
    methodology_version: str
    methodology_status: MethodologyStatus = "poc_unvalidated"
    capture_quality: CaptureQuality
    movement: MovementSummary
    repetitions: list[RepetitionSegment] = Field(default_factory=list, max_length=12)
    checklist: list[CriterionAssessment] = Field(min_length=1, max_length=16)
    primary_focus_criterion_id: SquatCriterionId | None = None
    positive_points: list[str] = Field(default_factory=list, max_length=6)
    priority_improvement: str | None = None
    secondary_improvements: list[str] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    muscle_activation_notice: str = (
        "O vídeo não mede ativação muscular, força, fadiga ou recrutamento por "
        "eletromiografia. As menções musculares descrevem apenas papéis esperados "
        "na literatura e não provam fraqueza, compensação ou hiperatividade."
    )
    literature_references: list[LiteratureReference] = Field(
        default_factory=list,
        max_length=12,
    )
    weighted_execution_score: WeightedExecutionScore | None = Field(
        default=None,
        description="Preenchido pelo serviço depois da resposta do Gemini.",
    )


class GeneralCaptureQuality(BaseModel):
    """Qualidade da captura para uma leitura genérica da tarefa identificada."""

    status: CaptureStatus
    confidence: Confidence
    detected_camera_angle: CaptureAngle = "unknown"
    exercise_visible: bool
    relevant_body_regions_visible: bool
    equipment_visible: bool
    target_person_trackable: bool
    other_people_visible: bool = False
    stable_camera: bool
    adequate_lighting: bool
    issues: list[str] = Field(default_factory=list, max_length=8)
    recapture_instructions: list[str] = Field(default_factory=list, max_length=6)


class GeneralRepetitionSegment(BaseModel):
    """Repetição genérica com tempos na linha do tempo original do vídeo."""

    index: int = Field(ge=1)
    complete: bool
    start_s: float | None = Field(default=None, ge=0)
    transition_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    confidence: Confidence
    observation: str


class GeneralMovementSummary(BaseModel):
    """Ritmo, amplitude e trajetória observáveis, sem inferir esforço interno."""

    exercise_detected: bool
    detected_repetitions: int = Field(ge=0)
    complete_repetitions: int = Field(ge=0)
    confidence: Confidence
    tempo_style: GeneralTempoStyle
    range_consistency: MovementConsistency
    tempo_consistency: MovementConsistency
    trajectory_consistency: MovementConsistency
    average_repetition_seconds: float | None = Field(default=None, gt=0)
    repetition_duration_variation: float | None = Field(default=None, ge=0)
    overall_observation: str


class GeneralExecutionReliability(BaseModel):
    """Confiabilidade da leitura visual, não probabilidade de estar correto."""

    level: Confidence
    coverage: float = Field(ge=0, le=1)
    evaluated_criteria: int = Field(ge=0)
    applicable_criteria: int = Field(ge=1)
    complete_repetitions: int = Field(ge=0)
    basis: list[str] = Field(default_factory=list, max_length=6)


class GeneralTrainingRelevance(BaseModel):
    """Interpretação condicional; nunca previsão de adaptação ou performance."""

    observed_style: GeneralTempoStyle
    observable_emphasis: list[str] = Field(default_factory=list, max_length=5)
    performance_interpretation: str
    cannot_determine_without: list[str] = Field(default_factory=list, max_length=8)


class GeneralExecutionSummary(BaseModel):
    classification: GeneralExecutionClassification
    reliability: GeneralExecutionReliability


class EquipmentIdentification(BaseModel):
    """Equipamento observado, sem inventar marca ou modelo."""

    category: EquipmentCategory
    name: str | None = Field(default=None, max_length=80)


class GeneralExecutionAnalysis(BaseModel):
    """Análise observacional geral para exercícios sem perfil específico."""

    analysis_mode: Literal["general_execution"] = "general_execution"
    exercise: ExerciseFamily
    exercise_label: str = Field(min_length=1, max_length=80)
    variation: str | None = Field(default=None, max_length=80)
    equipment: EquipmentIdentification
    methodology_version: str
    methodology_status: MethodologyStatus = "poc_unvalidated"
    capture_quality: GeneralCaptureQuality
    movement: GeneralMovementSummary
    repetitions: list[GeneralRepetitionSegment] = Field(
        default_factory=list,
        max_length=16,
    )
    checklist: list[CriterionAssessment] = Field(min_length=1, max_length=16)
    primary_focus_criterion_id: GeneralCriterionId | None = None
    execution_summary: GeneralExecutionSummary
    training_relevance: GeneralTrainingRelevance
    expected_muscle_roles: list[str] = Field(default_factory=list, max_length=8)
    positive_points: list[str] = Field(default_factory=list, max_length=6)
    priority_improvement: str | None = None
    secondary_improvements: list[str] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    muscle_activation_notice: str = (
        "O vídeo não mede ativação muscular, força, fadiga ou recrutamento por "
        "eletromiografia. Os grupos citados são papéis esperados para a família "
        "do exercício, não uma medição individual."
    )
    literature_references: list[LiteratureReference] = Field(
        default_factory=list,
        max_length=12,
    )
    weighted_execution_score: None = Field(
        default=None,
        description=(
            "A metodologia geral não publica nota numérica entre exercícios; "
            "usa classificação e cobertura observacional."
        ),
    )


AcademiaAnalysisMetrics = Annotated[
    SquatAnalysis | GeneralExecutionAnalysis,
    Field(discriminator="analysis_mode"),
]


class AcademiaRouteInfo(BaseModel):
    """Perfil/metodologia usados na análise, expostos para transparência."""

    exercise: ExerciseSlug
    exercise_label: str
    methodology_version: str
    methodology_status: MethodologyStatus
    methodology_scope: MethodologyScope = Field(
        default="exercise_specific",
        description=(
            "Fallback compatível das rotas específicas existentes. Rotas gerais "
            "devem declarar general_execution explicitamente."
        ),
    )
    capture_angle: CaptureAngle
    fps: int = Field(ge=1)
    media_resolution: str
    thinking_level: str
    schema_name: str
    duration_seconds: float | None = Field(default=None, ge=0)
    max_duration_seconds: float = Field(gt=0)


class TargetIdentification(BaseModel):
    """Resultado da continuidade visual da pessoa-alvo."""

    status: TargetStatus
    multiple_people_visible: bool


class ActiveExerciseInterval(BaseModel):
    """Intervalo temporal aproximado do exercício principal."""

    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_order(self) -> ActiveExerciseInterval:
        if self.end_s <= self.start_s:
            raise ValueError("end_s deve ser maior que start_s")
        return self


class ExerciseIdentification(BaseModel):
    """Identificação automática materializada e roteada por taxonomia local."""

    status: IdentificationStatus
    exercise_family: ExerciseFamily
    exercise_label: str = Field(min_length=1, max_length=80)
    variation: str | None = Field(default=None, max_length=80)
    confidence: Confidence
    equipment: EquipmentIdentification
    target: TargetIdentification
    multiple_exercises_visible: bool
    active_interval: ActiveExerciseInterval | None = None
    profile_slug: str | None = Field(default=None, max_length=80)
    methodology_available: bool
    methodology_scope: MethodologyScope = Field(
        default="none",
        description=(
            "Fallback fail-safe para identificação sem rota. Identificações com "
            "metodologia devem declarar exercise_specific ou general_execution."
        ),
    )
    reason: IdentificationReason


class AcademiaAnalysisResponse(BaseModel):
    """Envelope HTTP da análise de academia, paralelo ao de tênis."""

    ok: bool = True
    analysis_status: AnalysisStatus
    identification: ExerciseIdentification
    route: AcademiaRouteInfo | None = None
    practitioner: PractitionerHint | None = None
    metrics: AcademiaAnalysisMetrics | None = Field(
        default=None,
        description=(
            "Metodologia específica quando registrada; nos demais exercícios "
            "identificados, análise geral observacional sem checklist emprestado."
        )
    )
    narrative: str | None = None
    audio_base64: str | None = None
    audio_mime: str = "audio/wav"
    warnings: list[str] = Field(default_factory=list)
    persisted_id: int | None = None


class TargetDescriptionTranscription(BaseModel):
    """Saída estrita do Gemini antes da higienização local."""

    model_config = ConfigDict(extra="forbid")

    speech_detected: bool
    transcript: str = Field(default="", max_length=1200)


class TargetDescriptionTranscriptionResponse(BaseModel):
    """Texto revisável gerado de uma gravação curta e não persistida."""

    ok: bool = True
    transcript: str = Field(min_length=1, max_length=500)
    duration_seconds: float = Field(gt=0)
    truncated: bool = False


class AcademiaProfileSummary(BaseModel):
    exercise: str
    label: str
    methodology_version: str
    methodology_status: MethodologyStatus
    methodology_notice: str
    capture_guidance: list[str]


class RecommendedRepetitions(BaseModel):
    min: int = Field(ge=1)
    max: int = Field(ge=1)


class AcademiaIdentificationCapabilities(BaseModel):
    exercise: bool = True
    variation: bool = True
    equipment: bool = True
    multiple_people_targeting: bool = True
    active_interval: bool = True
    general_execution_analysis: bool = True


class AcademiaVoiceTranscriptionHealth(BaseModel):
    available: bool
    model: str
    max_duration_seconds: float = Field(gt=0)
    max_upload_mb: int = Field(ge=1)


class AcademiaHealthResponse(BaseModel):
    configured: bool
    analysis_model: str
    tts_model: str
    tts_voice: str
    analysis_fps: int = Field(ge=1)
    identification_fps: int = Field(ge=1)
    identification_mode: Literal["automatic"] = "automatic"
    automatic_identification: AcademiaIdentificationCapabilities = Field(
        default_factory=AcademiaIdentificationCapabilities
    )
    voice_transcription: AcademiaVoiceTranscriptionHealth
    max_upload_mb: int = Field(ge=1)
    max_duration_seconds: float = Field(gt=0)
    persistence_available: bool
    recommended_repetitions: RecommendedRepetitions
    profiles: list[AcademiaProfileSummary]


class AcademiaHistoryItem(BaseModel):
    id: int
    exercise: str
    methodology_version: str
    practitioner_id: str | None = None
    practitioner_name: str | None = None
    capture_angle: CaptureAngle
    analysis_status: AnalysisStatus
    created_at: datetime
    complete_repetitions: int | None = None
    execution_score: float | None = None
    priority_improvement: str | None = None
    has_audio: bool


class AcademiaHistoryResponse(BaseModel):
    items: list[AcademiaHistoryItem]
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    available: bool
    warning: str | None = None


class AcademiaStoredAnalysis(BaseModel):
    id: int
    exercise: str
    methodology_version: str
    practitioner_id: str | None = None
    practitioner_name: str | None = None
    capture_angle: CaptureAngle
    analysis_status: AnalysisStatus
    result_json: dict
    created_at: datetime
    has_audio: bool


class AcademiaDeleteResponse(BaseModel):
    deleted: Literal[True]
    id: int


__all__ = [
    "ActiveExerciseInterval",
    "AcademiaAnalysisMetrics",
    "AcademiaAnalysisResponse",
    "AcademiaDeleteResponse",
    "AcademiaHealthResponse",
    "AcademiaHistoryItem",
    "AcademiaHistoryResponse",
    "AcademiaIdentificationCapabilities",
    "AcademiaProfileSummary",
    "AcademiaRouteInfo",
    "AcademiaStoredAnalysis",
    "AcademiaVoiceTranscriptionHealth",
    "AnalysisStatus",
    "ApproximateCriterionState",
    "CaptureAngle",
    "CaptureQuality",
    "CaptureStatus",
    "Confidence",
    "CriterionAssessment",
    "CriterionVerdict",
    "EquipmentCategory",
    "EquipmentIdentification",
    "ExecutionScoreComponent",
    "ExerciseFamily",
    "ExerciseIdentification",
    "ExerciseIdentificationPass",
    "ExerciseSlug",
    "IdentificationReason",
    "IdentificationStatus",
    "GeneralCaptureQuality",
    "GeneralExecutionAnalysis",
    "GeneralExecutionCapturePass",
    "GeneralExecutionChecklistPass",
    "GeneralExecutionClassification",
    "GeneralExecutionReliability",
    "GeneralExecutionSummary",
    "GeneralCriterionId",
    "GeneralAlignmentPattern",
    "GeneralConsistencyPattern",
    "GeneralEquipmentPattern",
    "GeneralMovementSummary",
    "GeneralRangePattern",
    "GeneralRepetitionSegment",
    "GeneralStabilityPattern",
    "GeneralTempoPattern",
    "GeneralTempoStyle",
    "GeneralTrajectoryPattern",
    "GeneralTrainingRelevance",
    "GeneralTransitionPattern",
    "GeneralVisionCaptureQuality",
    "GeneralVisionMovementSummary",
    "GeneralVisionRepetitionSegment",
    "LiteratureReference",
    "MethodologyStatus",
    "MethodologyScope",
    "MovementPhase",
    "MovementConsistency",
    "MovementPhaseTimestamp",
    "MovementSummary",
    "PractitionerHint",
    "RecommendedRepetitions",
    "RepetitionSegment",
    "SquatAnalysis",
    "SquatCapturePass",
    "SquatChecklistPass",
    "SquatCriterionId",
    "TargetIdentification",
    "TargetDescriptionTranscription",
    "TargetDescriptionTranscriptionResponse",
    "TargetStatus",
    "VisionCaptureQuality",
    "VisionCriterionAssessment",
    "VisionMovementSummary",
    "VisionRepetitionSegment",
    "WeightedExecutionScore",
]
