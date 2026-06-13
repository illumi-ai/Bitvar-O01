"""Benchmarks ATP/WTA da literatura (blueprint §06).

Dois usos:

* ``BENCHMARK_TEXT`` — frases injetadas nos prompts para o modelo preencher os
  campos ``benchmark_reference`` com normas consistentes;
* ``BENCHMARK_NUMBERS`` — valores numéricos que o frontend usa nas barras de
  comparação (jogador × elite).

Valores de Grand Slams; variam por superfície — referência de calibração, não
gabarito fixo.
"""

from __future__ import annotations

# números por gênero × métrica (chaves batem com os campos do schema match)
BENCHMARK_NUMBERS: dict[str, dict[str, float]] = {
    "male": {
        "first_serve_points_won_pct": 70.0,
        "second_serve_points_won_pct": 57.0,
        "return_points_won_pct": 38.0,
        "avg_rally_length": 3.87,
        "rally_0_4_pct": 69.0,
        "rally_9_plus_pct": 10.3,
        "baseline_points_won_pct": 50.0,
        "net_points_won_pct": 65.0,
        "winner_to_ue_ratio": 1.0,
        "double_faults": 3.7,
        "aces": 10.0,
    },
    "female": {
        "first_serve_points_won_pct": 66.0,
        "second_serve_points_won_pct": 50.0,
        "return_points_won_pct": 47.0,
        "avg_rally_length": 3.87,
        "rally_0_4_pct": 68.0,
        "rally_9_plus_pct": 10.2,
        "baseline_points_won_pct": 50.0,
        "net_points_won_pct": 66.0,
        "winner_to_ue_ratio": 1.0,
        "double_faults": 4.0,
        "aces": 4.0,
    },
}

# métricas em que "menor é melhor" (barra invertida no dashboard)
LOWER_IS_BETTER = {"double_faults"}

# frases de referência por gênero × seção do schema match
BENCHMARK_TEXT: dict[str, dict[str, str]] = {
    "male": {
        "serve": "Elite ATP: 1º saque ~70% dos pontos ganhos, 2º saque ~57%; vencedores ~3.7 duplas faltas/jogo.",
        "return": "ATP: vencer games de devolução é raro e decisivo; ~35-40% dos pontos de devolução ganhos.",
        "rally": "ATP US Open 2024: ~3.87 golpes/ponto, rallies 0-4 ~69%, 9+ ~10.3%, pontos na rede ~65%.",
    },
    "female": {
        "serve": "Elite WTA: 1º saque ~62-70% dos pontos ganhos, 2º saque ~50% (janela de devolução maior).",
        "return": "WTA: ~+12% de games de devolução vs ATP; devolução é alavanca decisiva (~45-48% pts ganhos).",
        "rally": "WTA US Open 2024: ~3.87 golpes/ponto (idêntico ao ATP), 0-4 ~68%, 9+ ~10.2%, rede ~66%.",
    },
}


def benchmark_block(gender: str) -> str:
    """Texto multi-linha com as normas do gênero, para o system prompt do match."""
    t = BENCHMARK_TEXT[gender]
    return (
        f"- Saque: {t['serve']}\n"
        f"- Devolução: {t['return']}\n"
        f"- Rally: {t['rally']}\n"
        "- Winner/erro não forçado é o maior discriminador vencedor×perdedor em ambos os gêneros."
    )


def numbers_for(gender: str) -> dict[str, float]:
    return dict(BENCHMARK_NUMBERS[gender])
