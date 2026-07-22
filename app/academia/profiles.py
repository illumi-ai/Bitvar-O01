"""Perfis versionados de exercício para a vertical Academia.

Perfis específicos continuam reservados a metodologias próprias, como o
agachamento. Famílias conhecidas sem perfil específico recebem um perfil
observacional geral construído localmente. Esse fallback geral avalia somente
características visíveis da execução e nunca empresta critérios biomecânicos de
outro exercício, publica score numérico ou prevê adaptações futuras.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from pydantic import BaseModel

from .models import (
    CaptureAngle,
    ExerciseFamily,
    GeneralExecutionAnalysis,
    MethodologyScope,
    SquatAnalysis,
)


SQUAT_METHODOLOGY_VERSION = "squat_poc_v1"
SQUAT_WEIGHTING_MODEL = "squat_observational_poc_v1"
GENERAL_METHODOLOGY_VERSION = "general_execution_observational_v1"
GENERAL_WEIGHTING_MODEL = "general_execution_no_numeric_score_v1"


# Rótulos canônicos são definidos localmente. O texto livre devolvido pelo VLM
# pode detalhar a variação, mas nunca escolhe perfil/metodologia.
EXERCISE_FAMILY_LABELS: dict[ExerciseFamily, str] = {
    "squat": "Agachamento",
    "machine_squat": "Agachamento em máquina",
    "leg_press": "Leg press",
    "hinge": "Dobradiça de quadril",
    "lunge": "Afundo ou passada",
    "horizontal_press": "Empurrada horizontal",
    "vertical_press": "Empurrada vertical",
    "horizontal_pull": "Puxada horizontal",
    "vertical_pull": "Puxada vertical",
    "knee_extension": "Extensão de joelhos",
    "knee_flexion": "Flexão de joelhos",
    "elbow_flexion": "Flexão de cotovelos",
    "elbow_extension": "Extensão de cotovelos",
    "calf_raise": "Elevação de panturrilhas",
    "core": "Exercício de core",
    "other": "Outro exercício",
    "unknown": "Exercício não identificado",
}


@dataclass(frozen=True)
class CriterionProfile:
    id: str
    label: str
    description: str
    adequate_when: str
    correction_guidance: str
    muscle_context: str
    observable_angles: tuple[CaptureAngle, ...]
    weight: float


@dataclass(frozen=True)
class LiteratureReferenceProfile:
    citation: str
    url: str


@dataclass(frozen=True)
class ExerciseProfile:
    slug: str
    aliases: tuple[str, ...]
    label: str
    description: str
    methodology_version: str
    methodology_status: str
    methodology_notice: str
    schema_model: type[BaseModel]
    criteria: tuple[CriterionProfile, ...]
    literature_references: tuple[LiteratureReferenceProfile, ...]
    capture_guidance: tuple[str, ...]
    recommended_min_reps: int
    recommended_max_reps: int
    min_complete_reps: int
    min_scored_criteria: int
    weighting_model: str
    methodology_scope: MethodologyScope = "exercise_specific"
    exercise_family: ExerciseFamily | None = None
    expected_muscle_roles: tuple[str, ...] = ()
    observable_emphasis: tuple[str, ...] = ()

    def criterion(self, criterion_id: str) -> CriterionProfile | None:
        return next((item for item in self.criteria if item.id == criterion_id), None)

    @property
    def criterion_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.criteria)


SQUAT_CRITERIA: tuple[CriterionProfile, ...] = (
    CriterionProfile(
        id="stance_and_foot_position",
        label="Base e posição dos pés",
        description=(
            "Observe se a base permanece estável e compatível com a anatomia da pessoa, "
            "sem impor uma largura ou rotação universal dos pés."
        ),
        adequate_when="A base se mantém estável e os pés não mudam de posição durante as repetições.",
        correction_guidance="Ajustar a base antes de iniciar e mantê-la estável durante todo o ciclo.",
        muscle_context=(
            "A posição dos pés pode alterar a distribuição de momentos entre quadril "
            "e joelho. O vídeo não permite atribuir esse padrão à ativação de um "
            "músculo específico."
        ),
        observable_angles=("frontal", "posterior", "diagonal"),
        weight=0.10,
    ),
    CriterionProfile(
        id="foot_contact",
        label="Contato dos pés com o solo",
        description="Observe apoio visível e estável do pé, especialmente perda de contato do calcanhar.",
        adequate_when="Os pés aparentam manter apoio estável, sem elevação clara do calcanhar.",
        correction_guidance="Reduzir a amplitude e estabilizar o apoio antes de progredir.",
        muscle_context=(
            "Extensores de quadril e joelho e flexores plantares participam do "
            "agachamento; o contato visual do pé não mede a contribuição individual "
            "desses grupos."
        ),
        observable_angles=("lateral", "diagonal", "frontal"),
        weight=0.12,
    ),
    CriterionProfile(
        id="knee_tracking",
        label="Trajetória dos joelhos em relação aos pés",
        description=(
            "Observe qualitativamente se os joelhos acompanham a direção dos pés; "
            "não diagnostique valgo estrutural nem estime graus."
        ),
        adequate_when="A trajetória visível é controlada e coerente com a direção dos pés.",
        correction_guidance="Controlar a trajetória dos joelhos alinhada à direção dos pés, sem forçar amplitude.",
        muscle_context=(
            "Quadríceps e musculatura do quadril participam do controle da tarefa. "
            "Um desvio visual do joelho não prova fraqueza, inibição ou hiperatividade "
            "de um músculo específico."
        ),
        observable_angles=("frontal", "posterior", "diagonal"),
        weight=0.18,
    ),
    CriterionProfile(
        id="squat_depth",
        label="Profundidade observável e controlada",
        description=(
            "Observe a amplitude atingida com controle. Não existe profundidade universal obrigatória "
            "nesta POC e não se deve inferir limitação articular."
        ),
        adequate_when="A profundidade é consistente e ocorre sem perda visual clara de controle.",
        correction_guidance="Usar uma amplitude que permita manter apoio, equilíbrio e controle do tronco.",
        muscle_context=(
            "A profundidade modifica a demanda relativa dos extensores do joelho e "
            "do quadril; carga, técnica e anatomia também influenciam essa demanda."
        ),
        observable_angles=("lateral", "diagonal"),
        weight=0.15,
    ),
    CriterionProfile(
        id="trunk_control",
        label="Controle do tronco e da coluna",
        description=(
            "Observe estabilidade global e mudanças abruptas do tronco; não diagnostique a coluna "
            "nem exija uma postura única para todas as proporções corporais."
        ),
        adequate_when="O tronco se move de forma controlada, sem perda abrupta de estabilidade visível.",
        correction_guidance="Reduzir a amplitude ou o ritmo para manter o tronco estável durante descida e subida.",
        muscle_context=(
            "A inclinação do tronco altera a distribuição dos momentos entre "
            "extensores do quadril e do joelho, com participação estabilizadora da "
            "musculatura do tronco. A câmera não mede recrutamento muscular."
        ),
        observable_angles=("lateral", "diagonal"),
        weight=0.15,
    ),
    CriterionProfile(
        id="hip_knee_coordination",
        label="Coordenação entre quadril e joelhos",
        description="Observe se quadril e joelhos participam de forma coordenada na descida e na subida.",
        adequate_when="Descida e subida são contínuas, sem compensação abrupta visível entre quadril e joelhos.",
        correction_guidance="Praticar o ciclo em ritmo mais lento, coordenando quadril e joelhos.",
        muscle_context=(
            "Extensores do quadril, extensores do joelho e flexores plantares "
            "contribuem em conjunto. A coordenação visual não permite separar força "
            "ou ativação de cada grupo."
        ),
        observable_angles=("lateral", "diagonal", "frontal"),
        weight=0.12,
    ),
    CriterionProfile(
        id="tempo_control",
        label="Controle do ritmo",
        description="Observe descida, transição no fundo e subida sem queda livre ou impulso brusco.",
        adequate_when="O ritmo é controlado e razoavelmente consistente entre as repetições.",
        correction_guidance="Desacelerar a descida e evitar rebote ou pressa na mudança de direção.",
        muscle_context=(
            "O ritmo muda o tempo sob tensão dos grupos envolvidos, mas um vídeo "
            "comum não quantifica ativação, fadiga ou produção de força muscular."
        ),
        observable_angles=("frontal", "lateral", "posterior", "diagonal"),
        weight=0.08,
    ),
    CriterionProfile(
        id="bilateral_symmetry",
        label="Simetria visual entre os lados",
        description=(
            "Observe assimetrias grosseiras e repetidas; perspectiva de câmera pode criar falsa assimetria."
        ),
        adequate_when="Não há deslocamento lateral grosseiro e repetido claramente sustentado pelo vídeo.",
        correction_guidance="Repetir com câmera centralizada e reduzir amplitude se houver deslocamento persistente.",
        muscle_context=(
            "Uma assimetria visual não identifica automaticamente qual músculo "
            "contribuiu para o padrão e pode ser criada pela perspectiva da câmera."
        ),
        observable_angles=("frontal", "posterior"),
        weight=0.10,
    ),
)


SQUAT_PROFILE = ExerciseProfile(
    slug="squat",
    aliases=("squat", "agachamento", "agachamento livre", "agachamento_livre"),
    label="Agachamento",
    description="Análise visual educacional de poucas repetições de agachamento.",
    methodology_version=SQUAT_METHODOLOGY_VERSION,
    methodology_status="poc_unvalidated",
    methodology_notice=(
        "Checklist observacional provisório para demonstração. Não foi calibrado contra "
        "ground truth nem validado por especialista e não constitui avaliação clínica."
    ),
    schema_model=SquatAnalysis,
    criteria=SQUAT_CRITERIA,
    literature_references=(
        LiteratureReferenceProfile(
            citation=(
                "Bryanton et al. (2012), Effect of squat depth and barbell load "
                "on relative muscular effort in squatting."
            ),
            url="https://doi.org/10.1519/JSC.0b013e31826791a7",
        ),
        LiteratureReferenceProfile(
            citation=(
                "Lorenzetti et al. (2018), How to squat? Effects of stance width, "
                "foot placement angle and experience on motion and loading."
            ),
            url="https://doi.org/10.1186/s13018-018-0763-8",
        ),
        LiteratureReferenceProfile(
            citation=(
                "Lewis et al. (2023), Effect of trunk and shank position on the "
                "hip-to-knee moment ratio in a bilateral squat."
            ),
            url="https://doi.org/10.1016/j.ptsp.2023.03.005",
        ),
        LiteratureReferenceProfile(
            citation=(
                "Caterisano et al. (2002), Effect of back squat depth on EMG "
                "activity of superficial hip and thigh muscles."
            ),
            url="https://pubmed.ncbi.nlm.nih.gov/12173958/",
        ),
        LiteratureReferenceProfile(
            citation=(
                "Padua et al. (2012), Neuromuscular characteristics of individuals "
                "displaying excessive medial knee displacement."
            ),
            url="https://doi.org/10.4085/1062-6050-47.5.10",
        ),
    ),
    capture_guidance=(
        "Grave de 3 a 6 repetições, sem incluir o treino inteiro.",
        (
            "Mantenha a pessoa-alvo e o corpo inteiro, inclusive os pés, no quadro; "
            "outras pessoas podem aparecer se a pessoa-alvo continuar inequívoca."
        ),
        "Apoie a câmera em posição fixa, aproximadamente na altura do quadril.",
        "Use boa iluminação e roupa que permita ver o contorno geral do movimento.",
        "Prefira vista lateral ou diagonal para profundidade/tronco e frontal para joelhos/simetria.",
    ),
    recommended_min_reps=3,
    recommended_max_reps=6,
    min_complete_reps=1,
    min_scored_criteria=4,
    weighting_model=SQUAT_WEIGHTING_MODEL,
    methodology_scope="exercise_specific",
    exercise_family="squat",
    expected_muscle_roles=(
        "extensores dos joelhos",
        "extensores do quadril",
        "flexores plantares",
        "musculatura estabilizadora do tronco",
    ),
    observable_emphasis=(
        "coordenação visível entre quadril e joelhos",
        "amplitude mantida com apoio e controle",
        "trajetória e ritmo consistentes entre repetições",
    ),
)


PROFILES: dict[str, ExerciseProfile] = {SQUAT_PROFILE.slug: SQUAT_PROFILE}


# O mapa descreve papéis normalmente esperados para a família do exercício.
# Ele não representa medição individual, EMG, força ou recrutamento observado.
EXPECTED_MUSCLE_ROLES: dict[ExerciseFamily, tuple[str, ...]] = {
    "machine_squat": (
        "extensores dos joelhos",
        "extensores do quadril",
        "adutores do quadril como participantes da tarefa",
        "musculatura estabilizadora do tronco",
    ),
    "leg_press": (
        "extensores dos joelhos",
        "extensores do quadril",
        "adutores do quadril como participantes da tarefa",
    ),
    "hinge": (
        "extensores do quadril",
        "flexores dos joelhos como participantes conforme a variação",
        "musculatura estabilizadora do tronco",
    ),
    "lunge": (
        "extensores dos joelhos",
        "extensores do quadril",
        "estabilizadores do quadril e do tronco",
        "flexores plantares como participantes da tarefa",
    ),
    "horizontal_press": (
        "peitoral maior",
        "deltoide anterior",
        "extensores dos cotovelos",
        "estabilizadores da cintura escapular",
    ),
    "vertical_press": (
        "deltoides",
        "extensores dos cotovelos",
        "estabilizadores da cintura escapular",
        "musculatura estabilizadora do tronco",
    ),
    "horizontal_pull": (
        "retratores da cintura escapular",
        "latíssimo do dorso como participante conforme a trajetória",
        "deltoide posterior",
        "flexores dos cotovelos",
    ),
    "vertical_pull": (
        "latíssimo do dorso",
        "redondo maior como participante da tarefa",
        "flexores dos cotovelos",
        "estabilizadores da cintura escapular",
    ),
    "knee_extension": ("extensores dos joelhos",),
    "knee_flexion": (
        "flexores dos joelhos",
        "gastrocnêmio como participante conforme a posição",
    ),
    "elbow_flexion": (
        "flexores dos cotovelos",
        "bíceps braquial",
        "braquial",
        "braquiorradial conforme a posição do antebraço",
    ),
    "elbow_extension": ("extensores dos cotovelos",),
    "calf_raise": (
        "flexores plantares",
        "gastrocnêmio",
        "sóleo",
    ),
    "core": (
        "musculatura do tronco compatível com a tarefa identificada",
        "estabilizadores do quadril e da cintura escapular quando participam do apoio",
    ),
}


GENERAL_OBSERVABLE_EMPHASIS: dict[ExerciseFamily, tuple[str, ...]] = {
    "machine_squat": (
        "trajetória guiada e contato estável com os apoios da máquina",
        "coordenação de quadril e joelhos ao longo das repetições",
    ),
    "leg_press": (
        "contato dos pés com a plataforma e do tronco com os apoios",
        "trajetória controlada da plataforma ou do carro",
    ),
    "hinge": (
        "trajetória repetível do corpo e da resistência",
        "coordenação visível entre quadril, joelhos e tronco",
    ),
    "lunge": (
        "controle da base e da trajetória em cada lado",
        "consistência entre repetições e lados observáveis",
    ),
    "horizontal_press": (
        "trajetória de empurrada e retorno",
        "estabilidade dos apoios e da cintura escapular",
    ),
    "vertical_press": (
        "trajetória vertical ou diagonal de empurrada",
        "controle do tronco e dos apoios",
    ),
    "horizontal_pull": (
        "trajetória de puxada e retorno",
        "controle do tronco e da cintura escapular",
    ),
    "vertical_pull": (
        "trajetória vertical de puxada e retorno",
        "controle dos apoios e da cintura escapular",
    ),
    "knee_extension": (
        "trajetória da perna e contato com banco e rolete",
        "transições controladas sem impacto visível no equipamento",
    ),
    "knee_flexion": (
        "trajetória da perna e manutenção dos apoios",
        "transições controladas sem perda visível de contato",
    ),
    "elbow_flexion": (
        "trajetória do antebraço e controle do retorno",
        "estabilidade do tronco e do braço conforme a variação",
    ),
    "elbow_extension": (
        "trajetória do antebraço e controle do retorno",
        "estabilidade dos apoios e do braço conforme a variação",
    ),
    "calf_raise": (
        "amplitude e controle da elevação e do retorno",
        "estabilidade dos apoios ao longo da série",
    ),
    "core": (
        "manutenção do padrão corporal observável",
        "controle de trajetória ou sustentação conforme a tarefa",
    ),
}


GENERAL_LITERATURE_REFERENCES: tuple[LiteratureReferenceProfile, ...] = (
    LiteratureReferenceProfile(
        citation=(
            "American College of Sports Medicine (2026), Resistance Training "
            "Prescription for Muscle Function, Hypertrophy, and Physical "
            "Performance in Healthy Adults: An Overview of Reviews."
        ),
        url=(
            "https://acsm.org/science-spotlight-acsm-releases-new-position-"
            "stand-on-resistance-training/"
        ),
    ),
    LiteratureReferenceProfile(
        citation=(
            "Schoenfeld, Ogborn & Krieger (2015), Effect of repetition "
            "duration during resistance training on muscle hypertrophy."
        ),
        url="https://doi.org/10.1007/s40279-015-0304-0",
    ),
    LiteratureReferenceProfile(
        citation=(
            "Pallarés et al. (2021), Effects of range of motion on resistance "
            "training adaptations: systematic review and meta-analysis."
        ),
        url="https://doi.org/10.1111/sms.14006",
    ),
)


_ALL_OBSERVABLE_ANGLES: tuple[CaptureAngle, ...] = (
    "frontal",
    "lateral",
    "posterior",
    "diagonal",
)


GENERAL_CRITERIA: tuple[CriterionProfile, ...] = (
    CriterionProfile(
        id="range_pattern",
        label="Amplitude observável",
        description=(
            "Observe a extensão visível do ciclo e sua repetibilidade, sem exigir "
            "amplitude máxima ou universal e sem inferir mobilidade articular."
        ),
        adequate_when=(
            "A amplitude permanece consistente e ocorre sem perda visual clara de "
            "controle, respeitando o percurso da tarefa e do equipamento."
        ),
        correction_guidance=(
            "Use uma amplitude que permita manter trajetória, apoios e controle "
            "consistentes; uma regulagem individual deve ser confirmada presencialmente."
        ),
        muscle_context="Preenchido pelo perfil local da família do exercício.",
        observable_angles=_ALL_OBSERVABLE_ANGLES,
        weight=0.0,
    ),
    CriterionProfile(
        id="tempo_pattern",
        label="Ritmo observável",
        description=(
            "Classifique qualitativamente o ritmo. Movimento lento ou rápido não é "
            "erro por si só; observe perda de controle, pressa abrupta ou irregularidade."
        ),
        adequate_when=(
            "O ritmo, seja lento, moderado ou rápido, permanece visivelmente controlado."
        ),
        correction_guidance=(
            "Adote um ritmo que permita controlar a ida, a mudança de direção e o retorno."
        ),
        muscle_context="Preenchido pelo perfil local da família do exercício.",
        observable_angles=_ALL_OBSERVABLE_ANGLES,
        weight=0.0,
    ),
    CriterionProfile(
        id="trajectory_pattern",
        label="Controle da trajetória",
        description=(
            "Observe se corpo, membros, implemento ou partes móveis da máquina seguem "
            "uma trajetória repetível, sem estimar distâncias ou ângulos exatos."
        ),
        adequate_when=(
            "A trajetória permanece controlada e semelhante entre as repetições observáveis."
        ),
        correction_guidance=(
            "Reduza a pressa e repita o percurso mantendo o implemento ou a parte móvel "
            "da máquina sob controle."
        ),
        muscle_context="Preenchido pelo perfil local da família do exercício.",
        observable_angles=_ALL_OBSERVABLE_ANGLES,
        weight=0.0,
    ),
    CriterionProfile(
        id="stability_pattern",
        label="Estabilidade e apoios",
        description=(
            "Observe contatos com solo, banco, encosto, plataforma ou pegadores e "
            "movimentos acessórios repetidos, sem exigir imobilidade absoluta."
        ),
        adequate_when=(
            "Os apoios relevantes permanecem estáveis e o movimento acessório não "
            "interrompe o controle da tarefa."
        ),
        correction_guidance=(
            "Organize os apoios antes de iniciar e preserve os contatos relevantes "
            "durante todo o ciclo."
        ),
        muscle_context="Preenchido pelo perfil local da família do exercício.",
        observable_angles=_ALL_OBSERVABLE_ANGLES,
        weight=0.0,
    ),
    CriterionProfile(
        id="alignment_pattern",
        label="Alinhamento visível",
        description=(
            "Observe qualitativamente a relação entre segmentos, articulações e caminho "
            "do equipamento no plano visível, sem diagnosticar desvios estruturais."
        ),
        adequate_when=(
            "O alinhamento observável permanece coerente com a trajetória identificada "
            "e sem variação grosseira e repetida."
        ),
        correction_guidance=(
            "Reorganize a posição inicial e mantenha articulações e apoios acompanhando "
            "a trajetória visível, sem forçar uma postura universal."
        ),
        muscle_context="Preenchido pelo perfil local da família do exercício.",
        observable_angles=_ALL_OBSERVABLE_ANGLES,
        weight=0.0,
    ),
    CriterionProfile(
        id="equipment_pattern",
        label="Interação com equipamento",
        description=(
            "Quando aplicável, observe banco, encosto, plataforma, cabos, pegadores, "
            "rolos, pivôs visíveis e fim de curso, sem inventar modelo ou regulagem."
        ),
        adequate_when=(
            "Os contatos permanecem estáveis e o equipamento percorre seu caminho sem "
            "perda de controle ou impacto abrupto visível."
        ),
        correction_guidance=(
            "Revise a posição dos apoios e a regulagem com as instruções da máquina ou "
            "com um profissional presencial antes de repetir o percurso controlado."
        ),
        muscle_context="Preenchido pelo perfil local da família do exercício.",
        observable_angles=_ALL_OBSERVABLE_ANGLES,
        weight=0.0,
    ),
    CriterionProfile(
        id="repetition_consistency_pattern",
        label="Consistência entre repetições",
        description=(
            "Compare amplitude, ritmo, trajetória e apoios ao longo da série, sem inferir "
            "fadiga, esforço interno ou causa para uma eventual mudança."
        ),
        adequate_when=(
            "As repetições observáveis mantêm padrão semelhante do início ao fim."
        ),
        correction_guidance=(
            "Priorize repetições com o mesmo percurso e controle; interrompa a análise "
            "visual quando o vídeo não mostrar o padrão com clareza."
        ),
        muscle_context="Preenchido pelo perfil local da família do exercício.",
        observable_angles=_ALL_OBSERVABLE_ANGLES,
        weight=0.0,
    ),
    CriterionProfile(
        id="transition_pattern",
        label="Controle nas mudanças de direção",
        description=(
            "Observe a transição entre ida e retorno e o início/fim da repetição, "
            "procurando impulso, rebote, travamento ou impacto abrupto visível."
        ),
        adequate_when=(
            "As mudanças de direção são contínuas e controladas, sem impacto ou impulso "
            "grosseiro e repetido."
        ),
        correction_guidance=(
            "Desacelere antes da mudança de direção e inicie o retorno sem rebote ou "
            "impacto no equipamento."
        ),
        muscle_context="Preenchido pelo perfil local da família do exercício.",
        observable_angles=_ALL_OBSERVABLE_ANGLES,
        weight=0.0,
    ),
)


GENERAL_SUPPORTED_FAMILIES: tuple[ExerciseFamily, ...] = (
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
)


def _general_muscle_context(family: ExerciseFamily) -> str:
    roles = EXPECTED_MUSCLE_ROLES.get(family, ())
    role_text = ", ".join(roles) if roles else "grupos compatíveis com a tarefa"
    return (
        f"Papéis esperados para esta família: {role_text}. A lista é contexto "
        "educacional local; o vídeo não mede ativação, força, fadiga ou contribuição "
        "individual de qualquer músculo."
    )


def _general_criteria_for_family(
    family: ExerciseFamily,
) -> tuple[CriterionProfile, ...]:
    muscle_context = _general_muscle_context(family)
    return tuple(
        CriterionProfile(
            id=criterion.id,
            label=criterion.label,
            description=criterion.description,
            adequate_when=criterion.adequate_when,
            correction_guidance=criterion.correction_guidance,
            muscle_context=muscle_context,
            observable_angles=criterion.observable_angles,
            weight=criterion.weight,
        )
        for criterion in GENERAL_CRITERIA
    )


@lru_cache(maxsize=None)
def general_profile_for_family(
    family: ExerciseFamily | str,
) -> ExerciseProfile | None:
    """Cria o perfil observacional geral de uma família canônica conhecida."""

    if family not in GENERAL_SUPPORTED_FAMILIES:
        return None
    typed_family = family
    label = EXERCISE_FAMILY_LABELS[typed_family]
    return ExerciseProfile(
        slug=typed_family,
        aliases=(typed_family,),
        label=label,
        description=(
            f"Análise observacional geral da execução de {label.lower()}, sem "
            "metodologia biomecânica específica ou previsão de resultados."
        ),
        methodology_version=GENERAL_METHODOLOGY_VERSION,
        methodology_status="poc_unvalidated",
        methodology_notice=(
            "Checklist geral de execução visual, ainda não validado por especialista "
            "nem calibrado contra ground truth. Avalia somente características "
            "observáveis e não mede eficácia, esforço, ativação ou performance futura."
        ),
        schema_model=GeneralExecutionAnalysis,
        criteria=_general_criteria_for_family(typed_family),
        literature_references=GENERAL_LITERATURE_REFERENCES,
        capture_guidance=(
            "Grave de 3 a 8 repetições contínuas do mesmo exercício.",
            (
                "Mantenha visíveis a pessoa-alvo, as regiões corporais relevantes, "
                "os apoios e a trajetória do equipamento ou implemento."
            ),
            "Apoie a câmera em posição fixa e use iluminação uniforme.",
            (
                "Em máquinas, procure mostrar banco, encosto, plataforma, pegadores "
                "e a parte móvel sem ocultar o corpo."
            ),
            (
                "Use vista lateral ou diagonal para trajetória e amplitude; uma vista "
                "frontal ou posterior pode complementar alinhamento e simetria."
            ),
        ),
        recommended_min_reps=3,
        recommended_max_reps=8,
        min_complete_reps=1,
        min_scored_criteria=4,
        weighting_model=GENERAL_WEIGHTING_MODEL,
        methodology_scope="general_execution",
        exercise_family=typed_family,
        expected_muscle_roles=EXPECTED_MUSCLE_ROLES.get(typed_family, ()),
        observable_emphasis=GENERAL_OBSERVABLE_EMPHASIS.get(typed_family, ()),
    )


def _normalize_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\s_-]+", " ", text.strip().lower())


_ALIASES: dict[str, str] = {
    _normalize_key(alias): profile.slug
    for profile in PROFILES.values()
    for alias in profile.aliases
}


def get_profile(exercise: str | None = None) -> ExerciseProfile:
    """Retorna um perfil explicitamente solicitado.

    ``None`` nunca seleciona agachamento por fallback: o exercício deve ser
    identificado e convertido para uma família canônica antes do roteamento.
    """

    if exercise is None or not exercise.strip():
        raise ValueError("exercício obrigatório para selecionar uma metodologia")

    key = _normalize_key(exercise)
    slug = _ALIASES.get(key)
    if slug is None:
        supported = ", ".join(sorted(PROFILES))
        raise ValueError(
            f"exercício não suportado: {exercise!r} (disponível no MVP: {supported})"
        )
    return PROFILES[slug]


def profile_for_family(family: ExerciseFamily | str) -> ExerciseProfile | None:
    """Resolve uma família enum para metodologia local, sem usar rótulo livre."""

    if family == "squat":
        return SQUAT_PROFILE
    return general_profile_for_family(family)


def list_profiles() -> tuple[ExerciseProfile, ...]:
    return tuple(PROFILES.values())


__all__ = [
    "CriterionProfile",
    "EXERCISE_FAMILY_LABELS",
    "EXPECTED_MUSCLE_ROLES",
    "ExerciseProfile",
    "GENERAL_CRITERIA",
    "GENERAL_LITERATURE_REFERENCES",
    "GENERAL_METHODOLOGY_VERSION",
    "GENERAL_OBSERVABLE_EMPHASIS",
    "GENERAL_SUPPORTED_FAMILIES",
    "GENERAL_WEIGHTING_MODEL",
    "LiteratureReferenceProfile",
    "PROFILES",
    "SQUAT_CRITERIA",
    "SQUAT_METHODOLOGY_VERSION",
    "SQUAT_PROFILE",
    "SQUAT_WEIGHTING_MODEL",
    "general_profile_for_family",
    "get_profile",
    "list_profiles",
    "profile_for_family",
]
