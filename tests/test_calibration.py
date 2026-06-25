"""Conjunto de REGRESSAO da calibragem Caio 24/06 (WF7).

Transforma o gabarito do treinador Juca (lance 00000201) e os artefatos calibraveis
de WF1-WF5 num teste deterministico OFFLINE: sem rede, sem DB, Gemini NUNCA
instanciado. Valida o TEXTO dos prompts, a TABELA de pesos do clip, o GATE de regras
por categoria, a validacao do schema e os rotulos-ouro do fixture.

A validacao final "98% da hora" contra o video real e um passo HUMANO (Juca) — ver
o doc §05. Aqui so garantimos os artefatos que tornam esse resultado possivel.

Rodar (igual ao CLAUDE.md, sem rede/DB):
    DATABASE_URL="postgresql://x:x@localhost:5432/x" GEMINI_API_KEY="test-key" \
        python3 -m pytest tests/test_calibration.py -q
"""

import os
import sys
from pathlib import Path

# app.settings exige DATABASE_URL no import; tennis precisa de chave p/ configured.
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost:5432/x")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

# torna ``fixtures`` importavel independentemente do rootdir do pytest (nao ha
# tests/__init__.py, entao garantimos o diretorio deste arquivo no sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from fixtures import golden_case, golden_cases  # noqa: E402


# --------------------------------------------------------------------------- #
# 0. fixture de rotulos-ouro (passa contra o scaffold atual — so le o arquivo) #
# --------------------------------------------------------------------------- #
def test_golden_cases_load_and_shape():
    cases = golden_cases()
    assert cases, "nenhum golden case encontrado em tests/fixtures/"
    for c in cases:
        assert {"case_id", "sport", "expected", "forbidden_terms", "rule_gate"} <= set(c)


def test_golden_case_00000201_targets():
    gc = golden_case("00000201")
    assert gc["sport"] == "beach_tennis"
    # a saida-alvo: fase defensiva, golpe defensivo, e SEM 'smash'
    assert "defense" in gc["expected"]["action_phase"]
    assert "serve_return" in gc["expected"]["action_phase"]
    assert "drop_shot" in gc["expected"]["shot_identified"]
    assert "smash" in gc["forbidden_terms"]
    assert gc["expected"]["floating_ball_fault"] is True
    assert gc["expected"]["base_is_focus"] is True
    assert "finta" in gc["expected"]["tactical_events_includes"]
    assert gc["rule_gate"]["level"] == "amador"


# --------------------------------------------------------------------------- #
# (a) PROMPT do clip: taxonomia fase-primeiro + smash proibido + quadra ao lado #
# --------------------------------------------------------------------------- #
def test_clip_prompt_phase_first_taxonomy_and_bans_smash():
    from app.tennis.prompts import analysis_system_prompt
    sp = analysis_system_prompt("male", "clip")
    low = sp.lower()
    # regra dura: overhead_smash so em attack/net_play
    assert "overhead_smash" in sp and "proibido" in low
    assert "attack" in sp and "net_play" in sp
    # whitelist de recepcao/defesa
    assert "drop_shot" in sp and "deixadinha" in low
    # heuristica de desambiguacao ancorada nos membros inferiores
    assert "joelhos" in low and "saque" in low and "morre" in low
    # delimitacao da quadra-alvo + ignorar a quadra ao lado (WF6)
    assert ("quadra ao lado" in low) or ("adjacente" in low)
    assert ("ignore" in low) or ("descarte" in low)
    assert "rede" in low and "linha" in low and "início ao fim" in low
    # o match NAO recebe a taxonomia golpe-por-fase do clip
    assert "deixadinha" not in analysis_system_prompt("male", "match").lower()


# --------------------------------------------------------------------------- #
# (b) PESOS do clip: base/footwork > braco em fase defensiva, e soma 1.0       #
# --------------------------------------------------------------------------- #
def test_clip_weight_models_sum_to_one():
    from app.tennis.weights import CLIP_WEIGHT_MODELS
    for name, spec in CLIP_WEIGHT_MODELS.items():
        total = round(sum(w for w, _, _ in spec.values()), 6)
        assert total == 1.0, f"{name} soma {total}"


def test_clip_defensive_model_promotes_base_over_arm():
    from app.tennis.weights import CLIP_WEIGHT_MODELS, DEFENSIVE_CLIP_MODEL
    spec = CLIP_WEIGHT_MODELS[DEFENSIVE_CLIP_MODEL]
    base_keys = {"defensive_base_flexion", "stability_center_gravity",
                 "floating_ball_control", "split_step", "court_positioning",
                 "recovery_after_shot"}
    arm_keys = {"balance_and_posture", "contact_point", "preparation",
                "follow_through", "racket_path"}
    base_w = sum(w for k, (w, _, _) in spec.items() if k in base_keys)
    arm_w = sum(w for k, (w, _, _) in spec.items() if k in arm_keys)
    # piso explícito: 'base DOMINA' não pode degradar para 'base empata' (era só >).
    assert base_w >= 0.70 and arm_w <= 0.25, f"base DOMINA? base={base_w} braço={arm_w}"


def test_golden_00000201_low_base_yields_low_official_score():
    # o gabarito (recepcao mal baseada, bola flutuante) deve produzir nota baixa
    # mesmo com 'braco bonito' — a base pesa, nao o smash imaginado.
    from app.tennis.weights import compute_clip_weighted_score
    recepcao_ruim = {
        "lower_body_base": {
            "defensive_base_flexion": {"score": 2, "observation": "pernas estendidas"},
            "movement_base_flexion": {"score": 3, "observation": "x"},
            "stability_center_of_gravity": {"score": 3, "observation": "x"},
        },
        "footwork_and_movement": {
            "split_step": {"score": 3, "observation": "x"},
            "court_positioning": {"score": 4, "observation": "x"},
            "recovery_after_shot": {"score": 4, "observation": "x"},
        },
        "technical_execution": {
            "contact_point": {"score": 9, "observation": "x"},
            "racket_path": {"score": 9, "observation": "x"},
            "balance_and_posture": {"score": 4, "observation": "x"},
            "preparation": {"score": 6, "observation": "x"},
        },
        "floating_ball_fault": True,
    }
    ws = compute_clip_weighted_score(recepcao_ruim, "serve_return")
    assert ws["weighting_model"] == "clip_defensive_base_v1"
    assert ws["score"] < 45, ws["score"]


# --------------------------------------------------------------------------- #
# (c) NARRATIVA: esqueleto 4-tempos + few-shot Juca + guard-rail anti-smash    #
# --------------------------------------------------------------------------- #
def test_narrative_prompt_skeleton_fewshot_and_anti_smash_for_00000201():
    from app.tennis.prompts import JUCA_FEWSHOT, build_narrative_prompt
    recepcao = {
        "action_phase": "serve_return", "shot_identified": "drop_shot",
        "key_improvement": "Abaixe a base na recepção: joelhos flexionados.",
    }
    p = build_narrative_prompt(recepcao, "male", "clip", player_name="Cesar")
    # 4 tempos, na ordem
    assert "NOMEIE A FASE" in p and "MEMBROS INFERIORES" in p and "CORREÇÃO objetiva" in p
    assert p.index("NOMEIE A FASE") < p.index("MEMBROS INFERIORES") < p.index("CORREÇÃO objetiva")
    # few-shot real do Juca
    assert JUCA_FEWSHOT in p and "bola flutuante" in p and "raiz do movimento" in p
    # guard-rail: na fase defensiva, 'smash' banido e anti-hype duro
    assert 'NÃO use a palavra "smash"' in p
    assert "superlativos vazios" in p and "'belo'" in p
    # num ataque o ban duro some, mas esqueleto+few-shot ficam
    ataque = build_narrative_prompt({"action_phase": "attack"}, "male", "clip")
    assert 'NÃO use a palavra "smash"' not in ataque and JUCA_FEWSHOT in ataque


# --------------------------------------------------------------------------- #
# (d) GATE de regras por categoria: amador != cobra saida do fundo; pro cita   #
# --------------------------------------------------------------------------- #
def test_rules_gate_matches_golden_rule_gate():
    from app.tennis.rules import build_rules_block
    gc = golden_case("00000201")
    assert gc["rule_gate"]["level"] == "amador"
    # amador (o nivel do gabarito): NAO cobra a saida do fundo
    assert build_rules_block("male", "amador") is None
    # profissional: cita a regra (contexto)
    blk = build_rules_block("male", "profissional")
    assert blk is not None and "PARCEIRO DO SACADOR" in blk and "rede" in blk
    # feminino nao herda a regra masculina
    assert build_rules_block("female", "profissional") is None


# --------------------------------------------------------------------------- #
# (e) SCHEMA: ClipAnalysis aceita os campos novos opcionais e o dict minimo     #
# --------------------------------------------------------------------------- #
def _te():
    from app.tennis.models import ScoreObs, TechnicalExecution
    so = lambda n: ScoreObs(score=n, observation="x")  # noqa: E731
    return TechnicalExecution(preparation=so(7), contact_point=so(7), follow_through=so(7),
                              balance_and_posture=so(7), racket_path=so(7))


def test_clip_minimal_dict_still_validates():
    # invariante 1: nenhum campo novo e obrigatorio — o dict minimo do scaffold passa.
    from app.tennis.models import ClipAnalysis
    c = ClipAnalysis(analysis_mode="clip", gender_profile="male", shot_identified="forehand",
                     technical_execution=_te(), clip_quality_score=7.0, key_improvement="ok")
    assert c.phase_alternative is None and c.lower_body_base is None
    assert c.tactical_events is None and c.floating_ball_fault is None
    assert c.point_outcome_link is None


def test_clip_full_calibration_dict_validates():
    # o contrato pos-calibragem do 00000201 (fase defensiva, base, finta, espaco livre).
    from app.tennis.models import ClipAnalysis, LowerBodyBase, ScoreObs, TacticalEvent
    so = lambda n: ScoreObs(score=n, observation="base baixa")  # noqa: E731
    c = ClipAnalysis(
        analysis_mode="clip", gender_profile="male", shot_identified="drop_shot",
        action_phase="serve_return", phase_confidence="baixa",
        phase_alternative="defense",
        phase_alternative_rationale="bola vem do saque adversário e ele recua",
        lower_body_base=LowerBodyBase(defensive_base_flexion=so(3), stability_center_of_gravity=so(3)),
        floating_ball_fault=True,
        floating_ball_observation="pernas estendidas + raquete baixa => bola flutuou",
        technical_execution=_te(), clip_quality_score=4.0,
        key_improvement="Flexione mais os joelhos e baixe o centro de gravidade.",
        tactical_events=[
            TacticalEvent(event_type="finta", actor="adversario", description="adversário tentou a finta"),
            TacticalEvent(event_type="espaco_livre", actor="alvo", description="aproveitou o vazio"),
        ],
        point_outcome_link="não caiu na finta, aproveitou o vazio e finalizou no espaço livre",
    )
    d = c.model_dump()
    assert d["phase_alternative"] == "defense" and d["floating_ball_fault"] is True
    assert len(d["tactical_events"]) == 2
    # teto de 5 eventos
    with pytest.raises(ValidationError):
        ClipAnalysis(analysis_mode="clip", gender_profile="male", shot_identified="forehand",
                     technical_execution=_te(), clip_quality_score=7.0, key_improvement="ok",
                     tactical_events=[TacticalEvent(event_type="outro", description=f"e{i}")
                                      for i in range(6)])


# --------------------------------------------------------------------------- #
# (f) o FIXTURE DIRIGE o comportamento computado — não só pina o JSON           #
# --------------------------------------------------------------------------- #
def test_golden_00000201_expectations_drive_computed_artifacts():
    # liga as expectativas-ouro (antes dados mortos) aos ARTEFATOS calculados: a
    # fase-alvo roteia p/ o modelo defensivo, a base pesa mais que o braço NOS PESOS
    # reais, e o cenário do gabarito (base ruim + bola flutuante, braço bom) gera nota
    # OFICIAL abaixo do teto-ouro. Se a calibragem regredir, ESTE teste cai.
    from app.tennis.weights import (
        CLIP_WEIGHT_MODELS, DEFENSIVE_CLIP_MODEL, clip_weight_model_for_phase,
        compute_clip_weighted_score,
    )
    exp = golden_case("00000201")["expected"]
    for ph in exp["action_phase"]:
        assert clip_weight_model_for_phase(ph) == DEFENSIVE_CLIP_MODEL
    if exp.get("lower_body_base_weighs_more_than_arm"):
        spec = CLIP_WEIGHT_MODELS[DEFENSIVE_CLIP_MODEL]
        base = sum(w for k, (w, _, _) in spec.items()
                   if k in {"defensive_base_flexion", "stability_center_gravity",
                            "floating_ball_control", "split_step", "court_positioning",
                            "recovery_after_shot"})
        arm = sum(w for k, (w, _, _) in spec.items()
                  if k in {"balance_and_posture", "contact_point", "preparation",
                           "follow_through", "racket_path"})
        assert base > arm
    gabarito = {
        "lower_body_base": {"defensive_base_flexion": {"score": 2, "observation": "x"},
                            "stability_center_of_gravity": {"score": 3, "observation": "x"}},
        "footwork_and_movement": {"split_step": {"score": 3, "observation": "x"}},
        "technical_execution": {"contact_point": {"score": 9, "observation": "x"},
                                "racket_path": {"score": 9, "observation": "x"}},
        "floating_ball_fault": exp["floating_ball_fault"],
    }
    ws = compute_clip_weighted_score(gabarito, exp["action_phase"][0])
    assert ws["score"] / 10.0 <= exp["clip_quality_score_max"]   # 0-100 -> escala 0-10 do teto
