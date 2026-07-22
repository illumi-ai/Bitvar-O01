"""Score observacional determinístico da vertical Academia.

O VLM avalia cada critério em 0..10 e registra evidência; Python faz toda a
aritmética. Critérios não observáveis são omitidos e os pesos restantes são
renormalizados, mas o gate de captura impede publicar uma nota quando o vídeo
não sustenta uma avaliação minimamente útil.

Este score pertence à metodologia ``poc_unvalidated``: serve para uma demo de
coaching, não mede risco de lesão, qualidade clínica ou precisão de 95%.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .models import AnalysisStatus
from .profiles import ExerciseProfile, get_profile


_VERDICT_FALLBACK = {
    # Fallback só mantém compatibilidade se o modelo omitir a nota. A rubrica do
    # prompt exige nota explícita; nenhum cálculo livre é delegado ao VLM.
    "adequado": 0.85,
    "ajuste_leve": 0.65,
    "a_corrigir": 0.40,
}


def _as_dict(value: Any) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    return value if isinstance(value, dict) else {}


def _profile(value: ExerciseProfile | str | None) -> ExerciseProfile:
    if isinstance(value, ExerciseProfile):
        return value
    return get_profile(value)


def _criterion_normalized(item: dict) -> float | None:
    if item.get("verdict") in {"nao_avaliavel", "nao_aplicavel"}:
        return None
    raw = item.get("score")
    if raw is not None:
        try:
            return max(0.0, min(1.0, float(raw) / 10.0))
        except (TypeError, ValueError):
            return None
    return _VERDICT_FALLBACK.get(item.get("verdict"))


def _critical_capture_failure(metrics: dict) -> tuple[bool, list[str]]:
    capture = _as_dict(metrics.get("capture_quality"))
    movement = _as_dict(metrics.get("movement"))
    reasons: list[str] = []

    if capture.get("status") == "inadequate":
        reasons.append("a captura foi classificada como inadequada")
    if capture.get("exercise_visible") is not True or movement.get("exercise_detected") is not True:
        reasons.append("o agachamento não foi identificado com segurança")
    target_trackable = capture.get("target_person_trackable")
    if target_trackable is None:
        target_trackable = capture.get("single_person_visible")
    if target_trackable is not True:
        reasons.append("a pessoa-alvo não pôde ser acompanhada sem ambiguidade")
    try:
        complete_reps = int(movement.get("complete_repetitions") or 0)
    except (TypeError, ValueError):
        complete_reps = 0
    if complete_reps < 1:
        reasons.append("nenhuma repetição completa pôde ser segmentada")

    return bool(reasons), reasons


def compute_execution_score(
    metrics: dict | BaseModel,
    profile: ExerciseProfile | str | None = None,
) -> dict:
    """Calcula o score 0..100 e seu breakdown sem alterar ``metrics``.

    Regras:

    * ``nao_avaliavel`` nunca vira zero; o peso é removido e renormalizado;
    * captura inadequada ou ausência de repetição completa bloqueia a nota;
    * menos critérios que ``profile.min_scored_criteria`` bloqueiam a nota;
    * confiança mede observabilidade e não é usada para premiar/punir execução.
    """

    data = _as_dict(metrics)
    selected = _profile(profile or data.get("exercise"))
    raw_items = data.get("checklist") or []
    by_id: dict[str, dict] = {}
    for raw in raw_items:
        item = _as_dict(raw)
        criterion_id = str(item.get("id") or "").strip()
        if criterion_id and criterion_id not in by_id:
            by_id[criterion_id] = item

    evaluated: list[tuple[object, float]] = []
    for criterion in selected.criteria:
        normalized = _criterion_normalized(by_id.get(criterion.id, {}))
        if normalized is not None:
            evaluated.append((criterion, normalized))

    total_present_weight = sum(criterion.weight for criterion, _ in evaluated)
    present_ids = {criterion.id for criterion, _ in evaluated}
    breakdown: list[dict] = []
    candidate_score = 0.0
    for criterion in selected.criteria:
        item = by_id.get(criterion.id, {})
        normalized = _criterion_normalized(item)
        present = criterion.id in present_ids
        effective_weight = (
            criterion.weight / total_present_weight
            if present and total_present_weight > 0
            else 0.0
        )
        contribution = effective_weight * normalized * 100 if normalized is not None else 0.0
        candidate_score += contribution
        breakdown.append(
            {
                "criterion_id": criterion.id,
                "label": criterion.label,
                "weight": round(criterion.weight, 4),
                "effective_weight": round(effective_weight, 4),
                "normalized": round(normalized, 4) if normalized is not None else None,
                "contribution_points": round(contribution, 2),
                "present": present,
            }
        )

    criteria_present = len(evaluated)
    criteria_total = len(selected.criteria)
    coverage = criteria_present / criteria_total if criteria_total else 0.0
    capture_failed, capture_reasons = _critical_capture_failure(data)
    enough_criteria = criteria_present >= selected.min_scored_criteria
    valid = not capture_failed and enough_criteria and total_present_weight > 0

    notes: list[str] = []
    if capture_reasons:
        notes.append("nota bloqueada: " + "; ".join(capture_reasons))
    if not enough_criteria:
        notes.append(
            "nota bloqueada: apenas "
            f"{criteria_present}/{criteria_total} critérios observáveis; mínimo "
            f"{selected.min_scored_criteria}"
        )
    if valid and criteria_present < criteria_total:
        notes.append(
            f"score parcial renormalizado sobre {criteria_present}/{criteria_total} critérios observáveis"
        )
    notes.append("score de POC não validado; não representa diagnóstico ou risco de lesão")

    return {
        "score": round(candidate_score, 1) if valid else None,
        "weighting_model": selected.weighting_model,
        "methodology_version": selected.methodology_version,
        "valid": valid,
        "criteria_present": criteria_present,
        "criteria_total": criteria_total,
        "coverage": round(coverage, 4),
        "component_breakdown": breakdown,
        "note": " ".join(notes),
    }


def derive_analysis_status(
    metrics: dict | BaseModel,
    profile: ExerciseProfile | str | None = None,
) -> AnalysisStatus:
    """Converte captura/cobertura em estado de domínio para a resposta HTTP 200."""

    data = _as_dict(metrics)
    selected = _profile(profile or data.get("exercise"))
    capture_failed, _ = _critical_capture_failure(data)
    if capture_failed:
        return "recapture_required"

    capture = _as_dict(data.get("capture_quality"))
    movement = _as_dict(data.get("movement"))
    score = compute_execution_score(data, selected)
    try:
        complete_reps = int(movement.get("complete_repetitions") or 0)
    except (TypeError, ValueError):
        complete_reps = 0

    if (
        capture.get("status") == "limited"
        or capture.get("whole_body_visible") is not True
        or capture.get("feet_visible") is not True
        or complete_reps < selected.recommended_min_reps
        or score["criteria_present"] < score["criteria_total"]
    ):
        return "limited"
    return "complete"


__all__ = ["compute_execution_score", "derive_analysis_status"]
