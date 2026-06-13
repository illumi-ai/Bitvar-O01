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


def compute_weighted_score(metrics: dict, weight_model: str) -> dict:
    """Calcula o ``weighted_performance_score`` (0-100) + breakdown por componente.

    Componentes sem dado são listados com ``present=False`` e os pesos presentes
    são re-normalizados, de modo que as contribuições somam exatamente o score.
    """
    spec = WEIGHT_MODELS.get(weight_model)
    if spec is None:
        raise ValueError(f"modelo de peso desconhecido: {weight_model!r}")

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
        "weighting_model": weight_model,
        "component_breakdown": breakdown,
        "components_present": len(present),
        "components_total": len(spec),
    }
