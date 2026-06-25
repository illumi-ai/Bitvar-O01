"""Modelos de peso do ``weighted_performance_score`` (blueprint §4.3 e §5.1).

O score 0-100 é calculado **em Python** a partir das estatísticas brutas da
chamada 1 — não é pedido ao VLM (que erra aritmética). Cada componente é
normalizado para 0..1, multiplicado pelo seu peso e somado. Componentes sem dado
são omitidos e os pesos restantes são re-normalizados (não penaliza dado ausente).

Trocar os pesos aqui = recalibrar o produto (Fase 5 do roadmap). Os pesos somam
1.00 em cada modelo; os fundamentos estão nas tabelas do blueprint.
"""

from __future__ import annotations

from typing import Callable

MALE_MODEL = "male_serve_dominant_v1"
FEMALE_MODEL = "female_return_baseline_v1"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _get(metrics: dict, section: str, key: str):
    sec = metrics.get(section) or {}
    return sec.get(key)


def _ratio_norm(metrics: dict) -> float | None:
    """winner/UE: 1.0 = empate, >=2.0 = elite → normaliza em ratio/2."""
    oq = metrics.get("outcome_quality") or {}
    ratio = oq.get("winner_to_ue_ratio")
    if ratio is None:
        w, ue = oq.get("winners"), oq.get("unforced_errors")
        if w is not None and ue:
            ratio = w / ue
    return None if ratio is None else _clamp(ratio / 2.0)


def _pct_norm(metrics: dict, section: str, key: str) -> float | None:
    v = _get(metrics, section, key)
    return None if v is None else _clamp(v / 100.0)


def _breakpoints_norm(metrics: dict) -> float | None:
    """Média de break points salvos% e convertidos% (frações de contagens)."""
    pp = metrics.get("pressure_points") or {}
    fracs = []
    faced, saved = pp.get("break_points_faced"), pp.get("break_points_saved")
    if faced and saved is not None:
        fracs.append(_clamp(saved / faced))
    opp, conv = pp.get("break_points_opportunities"), pp.get("break_points_converted")
    if opp and conv is not None:
        fracs.append(_clamp(conv / opp))
    return sum(fracs) / len(fracs) if fracs else None


def _bp_converted_norm(metrics: dict) -> float | None:
    pp = metrics.get("pressure_points") or {}
    opp, conv = pp.get("break_points_opportunities"), pp.get("break_points_converted")
    return _clamp(conv / opp) if opp and conv is not None else None


def _aces_norm(metrics: dict) -> float | None:
    """Proxy de dominância de saque: aces saturando em ~12/partida."""
    aces = _get(metrics, "serve", "aces")
    return None if aces is None else _clamp(aces / 12.0)


def _double_fault_penalty(metrics: dict) -> float | None:
    """Inverso: 0 duplas faltas → 1.0; satura penalizando em ~8/partida."""
    df = _get(metrics, "serve", "double_faults")
    return None if df is None else _clamp(1.0 - df / 8.0)


def _return_norm(metrics: dict) -> float | None:
    """Devolução: prefere return_points_won_pct; cai p/ games vencidos (~6)."""
    pts = _get(metrics, "return", "return_points_won_pct")
    if pts is not None:
        return _clamp(pts / 100.0)
    games = _get(metrics, "return", "return_games_won")
    return None if games is None else _clamp(games / 6.0)


# componente → (peso, normalizador, rótulo legível)
_Component = tuple[float, Callable[[dict], "float | None"], str]

WEIGHT_MODELS: dict[str, dict[str, _Component]] = {
    MALE_MODEL: {
        "winner_to_ue_ratio":        (0.22, _ratio_norm, "Dominância (winners/erros)"),
        "first_serve_points_won":    (0.18, lambda m: _pct_norm(m, "serve", "first_serve_points_won_pct"), "1º saque — pts ganhos"),
        "second_serve_points_won":   (0.15, lambda m: _pct_norm(m, "serve", "second_serve_points_won_pct"), "2º saque — pts ganhos"),
        "break_points":              (0.15, _breakpoints_norm, "Break points (salvos+convertidos)"),
        "ace_and_serve_dominance":   (0.12, _aces_norm, "Aces / dominância de saque"),
        "rally_0_4_won":             (0.10, lambda m: _pct_norm(m, "rally", "baseline_points_won_pct"), "Jogo de fundo (rally curto)"),
        "net_points_won":            (0.08, lambda m: _pct_norm(m, "rally", "net_points_won_pct"), "Pontos na rede"),
    },
    FEMALE_MODEL: {
        "winner_to_ue_ratio":        (0.22, _ratio_norm, "Dominância (winners/erros)"),
        "return_games_won":          (0.18, _return_norm, "Jogo de devolução"),
        "second_serve_points_won":   (0.16, lambda m: _pct_norm(m, "serve", "second_serve_points_won_pct"), "2º saque + janela de devolução"),
        "break_points_converted":    (0.15, _bp_converted_norm, "Break points convertidos"),
        "first_serve_points_won":    (0.12, lambda m: _pct_norm(m, "serve", "first_serve_points_won_pct"), "1º saque — pts ganhos"),
        "baseline_points_won":       (0.12, lambda m: _pct_norm(m, "rally", "baseline_points_won_pct"), "Jogo de fundo de quadra"),
        "double_fault_penalty":      (0.05, _double_fault_penalty, "Penalidade de dupla falta (inverso)"),
    },
}

WEIGHT_MODEL_BY_GENDER = {"male": MALE_MODEL, "female": FEMALE_MODEL}


def _compute_from_spec(metrics: dict, model_name: str, spec: dict[str, _Component]) -> dict:
    """Núcleo compartilhado: normaliza, re-normaliza sobre presentes, soma 100.

    Componentes sem dado são listados com ``present=False`` e os pesos presentes
    são re-normalizados, de modo que as contribuições somam exatamente o score.
    Usado tanto pelo match (:func:`compute_weighted_score`) quanto pelo clip
    (:func:`compute_clip_weighted_score`).
    """
    present: list[tuple[str, float, float, str]] = []  # (nome, peso, normalizado, rótulo)
    breakdown: list[dict] = []
    for name, (weight, normalizer, label) in spec.items():
        value = normalizer(metrics)
        if value is None:
            breakdown.append({
                "component": name, "label": label, "weight": weight,
                "normalized": None, "contribution_pts": 0.0, "present": False,
            })
        else:
            present.append((name, weight, value, label))

    total_weight = sum(w for _, w, _, _ in present)
    score = 0.0
    for name, weight, value, label in present:
        contrib = (weight / total_weight) * value * 100 if total_weight else 0.0
        score += contrib
        breakdown.append({
            "component": name, "label": label, "weight": weight,
            "normalized": round(value, 4), "contribution_pts": round(contrib, 2),
            "present": True,
        })

    # mantém a ordem original dos componentes do modelo
    order = list(spec.keys())
    breakdown.sort(key=lambda b: order.index(b["component"]))

    return {
        "score": round(score, 1),
        "weighting_model": model_name,
        "component_breakdown": breakdown,
        "components_present": len(present),
        "components_total": len(spec),
    }


def compute_weighted_score(metrics: dict, weight_model: str) -> dict:
    """Calcula o ``weighted_performance_score`` (0-100) + breakdown por componente.

    Componentes sem dado são listados com ``present=False`` e os pesos presentes
    são re-normalizados, de modo que as contribuições somam exatamente o score.
    """
    spec = WEIGHT_MODELS.get(weight_model)
    if spec is None:
        raise ValueError(f"modelo de peso desconhecido: {weight_model!r}")
    return _compute_from_spec(metrics, weight_model, spec)


# --------------------------------------------------------------------------- #
# CLIP — ponderação condicionada à FAMÍLIA DE FASE (WF3, calibragem Caio 24/06) #
#                                                                             #
# Mesma mecânica do match (normaliza 0..1, pondera, re-normaliza sobre os      #
# componentes presentes, soma 100), mas a TABELA DE PESOS muda conforme a fase #
# do ponto: o especialista avalia a BASE/MEMBROS INFERIORES nas fases          #
# defensivas, e a execução de braço/contato nas ofensivas. Recalibrar = editar #
# CLIP_WEIGHT_MODELS (invariante 2: score em Python, nunca no VLM).            #
# --------------------------------------------------------------------------- #

DEFENSIVE_CLIP_MODEL = "clip_defensive_base_v1"   # serve_return / defense
OFFENSIVE_CLIP_MODEL = "clip_offensive_arm_v1"    # attack / net_play / serve
NEUTRAL_CLIP_MODEL = "clip_neutral_balanced_v1"   # baseline_rally / transition / unknown

# Aliases canônicos esperados por WF7 (mesmo objeto, nomes diferentes).
CLIP_DEFENSE_MODEL = DEFENSIVE_CLIP_MODEL
CLIP_WEIGHT_MODEL_DEFAULT = NEUTRAL_CLIP_MODEL

# eixo de FASE -> família de modelo de pesos do clip
CLIP_PHASE_FAMILY: dict[str, str] = {
    "serve_return": DEFENSIVE_CLIP_MODEL,
    "defense":      DEFENSIVE_CLIP_MODEL,
    "attack":       OFFENSIVE_CLIP_MODEL,
    "net_play":     OFFENSIVE_CLIP_MODEL,
    "serve":        OFFENSIVE_CLIP_MODEL,
    "baseline_rally": NEUTRAL_CLIP_MODEL,
    "transition":   NEUTRAL_CLIP_MODEL,
    "unknown":      NEUTRAL_CLIP_MODEL,
}


def clip_weight_model_for_phase(phase: str | None) -> str:
    """Família de pesos do clip a partir da fase; neutra como fallback seguro."""
    return CLIP_PHASE_FAMILY.get((phase or "").strip().lower(), NEUTRAL_CLIP_MODEL)


def _score_norm(section: str, key: str) -> Callable[[dict], "float | None"]:
    """Normalizador de um ScoreObs aninhado (``metrics[section][key].score``) em 0..1."""
    def _n(metrics: dict) -> float | None:
        sec = metrics.get(section) or {}
        obs = sec.get(key) or {}
        s = obs.get("score") if isinstance(obs, dict) else None
        return None if s is None else _clamp(s / 10.0)
    return _n


def _floating_ball_norm(metrics: dict) -> float | None:
    """Penalidade da bola flutuante — DETECTOR de falha, não recompensa.

    fault=True => 0.0 (puxa a nota pra baixo). fault=False OU ausente => None: o
    componente é omitido e os pesos re-normalizam, em vez de dar 0.12 de graça por
    'não ter falhado' (que inflava um clipe defensivo sem dado de base). É o gancho
    do padrão 'pernas estendidas + raquete baixa => bola que flutua' direto na nota.
    """
    return 0.0 if metrics.get("floating_ball_fault") is True else None


# Cada modelo soma 1.00. Forma 3-tupla idêntica ao WEIGHT_MODELS:
# componente -> (peso, normalizador, rótulo legível PT-BR).
CLIP_WEIGHT_MODELS: dict[str, dict[str, _Component]] = {
    # DEFENSIVA: base/membros inferiores + footwork DOMINAM; braço pesa pouco.
    DEFENSIVE_CLIP_MODEL: {
        "defensive_base_flexion":   (0.24, _score_norm("lower_body_base", "defensive_base_flexion"), "Flexão de joelhos na recepção/defesa"),
        "stability_center_gravity": (0.14, _score_norm("lower_body_base", "stability_center_of_gravity"), "Estabilidade do centro de gravidade"),
        "floating_ball_control":    (0.12, _floating_ball_norm, "Controle x bola flutuante (base+raquete)"),
        "split_step":               (0.14, _score_norm("footwork_and_movement", "split_step"), "Split step / prontidão"),
        "court_positioning":        (0.10, _score_norm("footwork_and_movement", "court_positioning"), "Posicionamento na recepção"),
        "recovery_after_shot":      (0.06, _score_norm("footwork_and_movement", "recovery_after_shot"), "Recuperação após o golpe"),
        "balance_and_posture":      (0.08, _score_norm("technical_execution", "balance_and_posture"), "Equilíbrio e postura"),
        "contact_point":            (0.07, _score_norm("technical_execution", "contact_point"), "Ponto de contato"),
        "preparation":              (0.05, _score_norm("technical_execution", "preparation"), "Preparação"),
    },
    # OFENSIVA: contato/trajetória/biomecânica pesam mais; base entra como apoio.
    OFFENSIVE_CLIP_MODEL: {
        "contact_point":            (0.20, _score_norm("technical_execution", "contact_point"), "Ponto de contato (alto/firme)"),
        "racket_path":              (0.18, _score_norm("technical_execution", "racket_path"), "Trajetória da raquete"),
        "kinetic_chain":            (0.14, _score_norm("biomechanics", "kinetic_chain"), "Cadeia cinética"),
        "hip_shoulder_rotation":    (0.12, _score_norm("biomechanics", "hip_shoulder_rotation"), "Rotação quadril-ombro"),
        "weight_transfer":          (0.10, _score_norm("biomechanics", "weight_transfer"), "Transferência de peso"),
        "follow_through":           (0.08, _score_norm("technical_execution", "follow_through"), "Finalização"),
        "movement_base_flexion":    (0.08, _score_norm("lower_body_base", "movement_base_flexion"), "Flexão da base no ataque"),
        "preparation":              (0.06, _score_norm("technical_execution", "preparation"), "Preparação"),
        "split_step":               (0.04, _score_norm("footwork_and_movement", "split_step"), "Split step"),
    },
    # NEUTRA: braço e base equilibrados (fundo de quadra / transição / desconhecido).
    NEUTRAL_CLIP_MODEL: {
        "contact_point":            (0.16, _score_norm("technical_execution", "contact_point"), "Ponto de contato"),
        "racket_path":              (0.12, _score_norm("technical_execution", "racket_path"), "Trajetória da raquete"),
        "balance_and_posture":      (0.12, _score_norm("technical_execution", "balance_and_posture"), "Equilíbrio e postura"),
        "defensive_base_flexion":   (0.12, _score_norm("lower_body_base", "defensive_base_flexion"), "Flexão de base"),
        "kinetic_chain":            (0.12, _score_norm("biomechanics", "kinetic_chain"), "Cadeia cinética"),
        "hip_shoulder_rotation":    (0.10, _score_norm("biomechanics", "hip_shoulder_rotation"), "Rotação quadril-ombro"),
        "split_step":               (0.10, _score_norm("footwork_and_movement", "split_step"), "Split step"),
        "court_positioning":        (0.08, _score_norm("footwork_and_movement", "court_positioning"), "Posicionamento"),
        "preparation":              (0.08, _score_norm("technical_execution", "preparation"), "Preparação"),
    },
}


# componentes que DEFINEM cada família — se NENHUM vier preenchido, a nota perde o
# sentido do modelo (ex.: fase defensiva sem nenhum dado de base/footwork acabaria
# pontuada só pelo braço, invertendo o DoD WF3). Nesse caso sinalizamos baixa confiança.
_CLIP_MODEL_AXIS: dict[str, set[str]] = {
    DEFENSIVE_CLIP_MODEL: {
        "defensive_base_flexion", "stability_center_gravity", "floating_ball_control",
        "split_step", "court_positioning", "recovery_after_shot",
    },
    OFFENSIVE_CLIP_MODEL: {
        "contact_point", "racket_path", "kinetic_chain", "hip_shoulder_rotation",
        "weight_transfer", "follow_through",
    },
}


def compute_clip_weighted_score(metrics: dict, phase: str | None, gender: str | None = None) -> dict:
    """Score 0-100 do CLIP, ponderado conforme a FAMÍLIA DE FASE.

    Mesmo shape do match (score/weighting_model/component_breakdown/
    components_present/total) para reuso direto do frontend. ``gender`` é aceito
    por simetria com o match e para futura calibração por gênero (hoje não altera
    a tabela — beach tennis exigiria rubrica do treinador). Se o eixo que DEFINE o
    modelo (base/footwork no defensivo; braço/biomecânica no ofensivo) não tiver
    NENHUM componente avaliado, anexa ``axis_incomplete``/``note``: a nota existe
    mas é pouco confiável (vira aviso, nunca derruba a análise).
    """
    model_name = clip_weight_model_for_phase(phase)
    spec = CLIP_WEIGHT_MODELS[model_name]
    result = _compute_from_spec(metrics, model_name, spec)
    axis = _CLIP_MODEL_AXIS.get(model_name)
    if axis is not None:
        present = {b["component"] for b in result["component_breakdown"] if b["present"]}
        if not (axis & present):
            result["axis_incomplete"] = True
            result["note"] = (
                f"nota pouco confiável: nenhum componente do eixo dominante do modelo "
                f"'{model_name}' foi avaliado (ex.: base/footwork na fase defensiva)."
            )
    return result
