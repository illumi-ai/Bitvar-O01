"""Testes do módulo de tênis — sem rede (Gemini é mockado)."""

import asyncio
import io
import os
import wave

# app.settings exige DATABASE_URL no import; o Gemini precisa de chave p/ configured.
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost:5432/x")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastapi import HTTPException, UploadFile  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.tennis import gemini as gem  # noqa: E402
from app.tennis import router as trouter  # noqa: E402
from app.tennis.config import tennis_settings as cfg  # noqa: E402
from app.tennis.models import (  # noqa: E402
    Biomechanics, ClipAnalysis, FootworkMovement, LowerBodyBase, MatchAnalysis,
    OutcomeQuality, PositioningRead, PressurePoints, RallyStats, ReturnStats,
    ScoreObs, ServeStats, SubjectHint, TacticalEvent, TacticalIntent, TechnicalExecution,
)
from app.tennis.routing import (  # noqa: E402
    build_route, decide_mode, normalize_gender, normalize_level, normalize_mode_override,
)
from app.tennis.service import EmptyUpload, TennisService, UploadTooLarge  # noqa: E402
from app.tennis.weights import (  # noqa: E402
    FEMALE_MODEL, MALE_MODEL, WEIGHT_MODELS, compute_weighted_score,
)


# --------------------------------------------------------------------------- #
# roteamento                                                                   #
# --------------------------------------------------------------------------- #
def test_normalize_gender_aliases():
    assert normalize_gender("Masculino") == "male"
    assert normalize_gender("f") == "female"
    assert normalize_gender(None) == "male"
    with pytest.raises(ValueError):
        normalize_gender("nope")


def test_decide_mode_always_clip():
    # modo PARTIDA removido: qualquer entrada vira clip
    assert decide_mode(30, None, None)[0] == "clip"
    assert decide_mode(600, None, None)[0] == "clip"               # antes seria match
    assert decide_mode(74.9, None, None)[0] == "clip"
    assert decide_mode(None, None, 300 * 1024 * 1024)[0] == "clip"  # arquivo grande tb é clip
    assert decide_mode(None, None, None)[0] == "clip"              # default
    assert decide_mode(600, "match", None)[0] == "clip"            # override 'match' antigo é ignorado


def test_mode_override_normalization():
    assert normalize_mode_override("auto") is None
    assert normalize_mode_override(None) is None
    assert normalize_mode_override("MATCH") == "match"
    with pytest.raises(ValueError):
        normalize_mode_override("bogus")


def test_build_route_is_always_clip():
    clip = build_route("male", duration=10, override=None, file_size_bytes=5_000_000)
    assert clip.info.mode == "clip" and clip.info.fps == cfg.clip_fps
    assert clip.info.media_resolution == cfg.clip_media_resolution
    assert clip.schema_model is ClipAnalysis and clip.weight_model is None

    # vídeo longo (antes 'match') e override 'match' agora também caem em clip
    longo = build_route("female", duration=600, override="match", file_size_bytes=3 * 10**8)
    assert longo.info.mode == "clip" and longo.schema_model is ClipAnalysis
    assert longo.weight_model is None


# --------------------------------------------------------------------------- #
# pesos / score ponderado                                                      #
# --------------------------------------------------------------------------- #
def test_weight_models_sum_to_one():
    for name, spec in WEIGHT_MODELS.items():
        total = round(sum(w for w, _, _ in spec.values()), 6)
        assert total == 1.0, f"{name} soma {total}"


def _full_match_metrics():
    return {
        "serve": {"first_serve_points_won_pct": 70, "second_serve_points_won_pct": 55,
                  "aces": 8, "double_faults": 3},
        "return": {"return_points_won_pct": 42, "return_games_won": 4},
        "rally": {"baseline_points_won_pct": 52, "net_points_won_pct": 66},
        "outcome_quality": {"winners": 30, "unforced_errors": 20},
        "pressure_points": {"break_points_faced": 6, "break_points_saved": 4,
                            "break_points_opportunities": 8, "break_points_converted": 3},
    }


def test_compute_weighted_score_contributions_sum_to_score():
    for model in (MALE_MODEL, FEMALE_MODEL):
        ws = compute_weighted_score(_full_match_metrics(), model)
        assert 0 <= ws["score"] <= 100
        assert ws["components_present"] == ws["components_total"]
        csum = round(sum(c["contribution_pts"] for c in ws["component_breakdown"]), 1)
        assert abs(csum - ws["score"]) < 0.2


def test_compute_weighted_score_renormalizes_missing_data():
    ws = compute_weighted_score(
        {"outcome_quality": {"winners": 10, "unforced_errors": 10}}, MALE_MODEL
    )
    assert ws["components_present"] == 1          # só o ratio tinha dado
    assert ws["score"] == 50.0                    # ratio 1.0 → 0.5 → 50, re-normalizado
    missing = [c for c in ws["component_breakdown"] if not c["present"]]
    assert all(c["contribution_pts"] == 0.0 for c in missing)


def test_double_fault_penalty_is_inverse():
    few = compute_weighted_score({"serve": {"double_faults": 0}}, FEMALE_MODEL)
    many = compute_weighted_score({"serve": {"double_faults": 8}}, FEMALE_MODEL)
    assert few["score"] > many["score"]           # menos duplas faltas → score maior


def test_unknown_weight_model_raises():
    with pytest.raises(ValueError):
        compute_weighted_score({}, "inexistente_v9")


# --------------------------------------------------------------------------- #
# áudio / WAV / TTS helpers                                                    #
# --------------------------------------------------------------------------- #
def test_pcm_to_wav_header():
    wav = gem._pcm_to_wav(b"\x00\x01" * 12000, 24000, 1, 2)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)


def test_rate_from_mime():
    assert gem._rate_from_mime("audio/L16;rate=16000", 24000) == 16000
    assert gem._rate_from_mime("audio/L16; codec=pcm; rate=22050", 24000) == 22050
    assert gem._rate_from_mime(None, 24000) == 24000
    assert gem._rate_from_mime("audio/wav", 24000) == 24000


def test_split_for_tts_respects_max():
    chunks = gem._split_for_tts("Uma frase. " * 500, 1800)
    assert len(chunks) > 1
    assert all(len(c) <= 1800 for c in chunks)
    short = gem._split_for_tts("Curto.", 1800)
    assert short == ["Curto."]


def test_split_for_tts_hard_caps_oversized_sentence():
    # frase única sem pontuação, maior que o limite → fatiada no braço
    chunks = gem._split_for_tts("a" * 5000 + ".", 1800)
    assert len(chunks) >= 3
    assert all(len(c) <= 1800 for c in chunks)


def test_extract_audio_finds_inline_data():
    inline = SimpleNamespace(data=b"PCM", mime_type="audio/L16;rate=24000")
    part = SimpleNamespace(inline_data=inline)
    resp = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])
    data, mime = gem._extract_audio(resp)
    assert data == b"PCM" and "24000" in mime


# --------------------------------------------------------------------------- #
# guarda de Content-Length (413 antes de ler o corpo)                          #
# --------------------------------------------------------------------------- #
def _req_with_content_length(value: int) -> Request:
    scope = {"type": "http", "headers": [(b"content-length", str(value).encode())]}
    return Request(scope)


def test_enforce_content_length_rejects_oversize():
    with pytest.raises(HTTPException) as ei:
        trouter._enforce_content_length(_req_with_content_length(cfg.max_upload_bytes + 1))
    assert ei.value.status_code == 413


def test_enforce_content_length_allows_within_limit():
    trouter._enforce_content_length(_req_with_content_length(1024))  # não levanta


# --------------------------------------------------------------------------- #
# guarda de tamanho no streaming (UploadTooLarge / EmptyUpload)                #
# --------------------------------------------------------------------------- #
def test_save_upload_too_large(monkeypatch):
    svc = TennisService(cfg)
    monkeypatch.setattr(cfg, "max_upload_mb", 0)  # qualquer byte estoura
    uf = UploadFile(filename="x.mp4", file=io.BytesIO(b"x" * 4096))
    with pytest.raises(UploadTooLarge):
        asyncio.run(svc._save_upload(uf))


def test_save_upload_empty():
    svc = TennisService(cfg)
    uf = UploadFile(filename="x.mp4", file=io.BytesIO(b""))
    with pytest.raises(EmptyUpload):
        asyncio.run(svc._save_upload(uf))


def test_safe_suffix_sanitizes():
    from app.tennis.service import _safe_suffix
    assert _safe_suffix("clip.mp4") == ".mp4"
    assert _safe_suffix("CLIP.MOV") == ".mov"
    assert _safe_suffix("name.\x00mp4") == ".mp4"      # null-byte saneado
    assert _safe_suffix("noext") == ".mp4"
    assert _safe_suffix("x." + "y" * 50) == ".mp4"      # extensão absurda


def test_save_upload_weird_filename_does_not_raise():
    # nome com null-byte não deve estourar mkstemp (antes virava 422 enganoso)
    svc = TennisService(cfg)
    uf = UploadFile(filename="bad.\x00mp4", file=io.BytesIO(b"\x00" * 32))
    path, size = asyncio.run(svc._save_upload(uf))
    try:
        assert size == 32 and path.endswith(".mp4") and os.path.exists(path)
    finally:
        os.remove(path)


# --------------------------------------------------------------------------- #
# endpoint /tennis/analyze com Gemini mockado                                  #
# --------------------------------------------------------------------------- #
def _clip_result() -> ClipAnalysis:
    so = lambda n: ScoreObs(score=n, observation="visível no vídeo")  # noqa: E731
    return ClipAnalysis(
        analysis_mode="clip", gender_profile="male", shot_identified="forehand",
        action_phase="baseline_rally", phase_confidence="alta", shot_confidence="media",
        approx_timestamp_s=3.5, visual_evidence="forehand de fundo, lado direito da quadra",
        subject_lock_confidence="alta", handedness="destro",
        positioning=PositioningRead(
            observed_zone="fundo", observed_side="direita",
            recommended_zone="meio", recommended_side="centro",
            rationale="recue meio passo e cubra o centro após o golpe.",
        ),
        technical_execution=TechnicalExecution(
            preparation=so(7), contact_point=so(8), follow_through=so(6),
            balance_and_posture=so(7), racket_path=so(8),
        ),
        footwork_and_movement=FootworkMovement(
            split_step=so(6), court_positioning=so(7), recovery_after_shot=so(6)
        ),
        biomechanics=Biomechanics(kinetic_chain=so(7), hip_shoulder_rotation=so(6), weight_transfer=so(7)),
        tactical_intent=TacticalIntent(shot_placement_quality=so(7), shot_selection=so(8)),
        lower_body_base=LowerBodyBase(
            defensive_base_flexion=so(7), movement_base_flexion=so(6),
            stability_center_of_gravity=so(7),
        ),
        floating_ball_fault=False,
        tactical_events=[
            TacticalEvent(event_type="finta", actor="adversario", approx_timestamp_s=2.1,
                          description="adversário amagou a finalização e errou o tempo da finta"),
            TacticalEvent(event_type="espaco_livre", actor="alvo", approx_timestamp_s=3.0,
                          description="alvo finalizou no vazio aberto pelo deslocamento da dupla"),
        ],
        point_outcome_link="não caiu na finta, aproveitou o deslocamento da dupla e finalizou no espaço livre",
        clip_quality_score=7.4, key_improvement="Gire mais o quadril no contato.",
        secondary_improvements=["Antecipe o split step."],
    )


def _match_result() -> MatchAnalysis:
    return MatchAnalysis(
        analysis_mode="match", gender_profile="female",
        serve=ServeStats(first_serve_points_won_pct=66, second_serve_points_won_pct=50,
                         aces=3, double_faults=4),
        return_=ReturnStats(return_points_won_pct=47, return_games_won=4),
        rally=RallyStats(avg_rally_length=3.9, rally_0_4_pct=68, rally_5_8_pct=22,
                         rally_9_plus_pct=10, baseline_points_won_pct=51, net_points_won_pct=64),
        outcome_quality=OutcomeQuality(winners=25, unforced_errors=22, winner_to_ue_ratio=1.14),
        pressure_points=PressurePoints(break_points_faced=5, break_points_saved=3,
                                       break_points_opportunities=7, break_points_converted=3),
        key_improvement="Aumente a consistência na devolução de 2º saque.",
    )


class _FakeGemini:
    def upload_video(self, path, mime_type=None):
        return SimpleNamespace(name="files/x", uri="https://files/x",
                               mime_type="video/mp4", state=SimpleNamespace(name="ACTIVE"))

    def delete_file(self, file):
        pass

    def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
        return _clip_result() if schema_model is ClipAnalysis else _match_result()

    def narrate(self, metrics, *, gender, mode, player_name=None):
        return "Você jogou muito bem. Continue treinando o quadril."

    def synthesize(self, narrative):
        return gem._pcm_to_wav(b"\x00\x01" * 2000, 24000, 1, 2)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(trouter.service, "gemini", _FakeGemini())
    monkeypatch.setattr(cfg, "gemini_api_key", "test-key")
    from app.main import app
    return TestClient(app)


def test_analyze_clip_three_outputs(client):
    r = client.post(
        "/tennis/analyze",
        files={"file": ("clip.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "true"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"]["mode"] == "clip"
    assert body["route"]["fps"] == cfg.clip_fps
    assert body["metrics"]["clip_quality_score"] == 7.4
    assert body["metrics"]["key_improvement"]
    # eixo de fase + posicionamento fluem do schema p/ as métricas (feedback Caio)
    assert body["metrics"]["action_phase"] == "baseline_rally"
    assert body["metrics"]["handedness"] == "destro"
    assert body["metrics"]["positioning"]["recommended_zone"] == "meio"
    assert body["narrative"]
    assert body["audio_base64"]               # saída 3 presente


def test_analyze_clip_records_camera_meta(client):
    r = client.post(
        "/tennis/analyze",
        files={"file": ("clip.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "camera_position": "central"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["metrics"]["camera_meta"]["position"] == "central"


def test_subject_block_and_prompt_injection():
    from app.tennis.prompts import analysis_system_prompt, build_narrative_prompt, build_subject_block
    assert build_subject_block() is None
    block = build_subject_block(name="João", outfit="camiseta azul", side="fundo esquerdo")
    assert block and "João" in block and "camiseta azul" in block and "fundo esquerdo" in block
    assert "VÁRIAS" in block  # avisa que o take pode ter várias pessoas
    # injeção no system prompt
    sp = analysis_system_prompt("male", "clip", block)
    assert "camiseta azul" in sp and "SOMENTE" in sp
    assert "camiseta azul" not in analysis_system_prompt("male", "clip")  # sem bloco, sem dica
    # narrativa personaliza pelo nome
    assert "João" in build_narrative_prompt({}, "male", "clip", player_name="João")
    assert "João" not in build_narrative_prompt({}, "male", "clip")


def test_subject_block_non_biometric_anchoring():
    from app.tennis.prompts import build_subject_block
    block = build_subject_block(
        outfit="camiseta azul", handedness="canhoto", headwear="boné branco",
        racket_color="preta e verde", glasses=True,
    )
    assert block and "canhoto" in block and "boné branco" in block and "preta e verde" in block
    assert "óculos" in block.lower()
    # ancoragem por aparência, jamais por rosto (spec A2 inviável)
    assert "reconhecimento facial" in block.lower()
    # só óculos True aparece; False/None não cria ruído
    assert build_subject_block(glasses=False) is None


def test_build_camera_block_central_convention():
    from app.tennis.prompts import analysis_system_prompt, build_camera_block
    assert build_camera_block(None) is None
    assert build_camera_block("") is None
    central = build_camera_block("central")
    assert central and "ESQUERDO" in central and "DIREITO" in central
    lateral = build_camera_block("lateral")
    assert lateral and "lateral" in lateral
    # injetado no system prompt junto da identificação
    sp = analysis_system_prompt("male", "clip", "ID DO JOGADOR", central)
    assert "CÂMERA" in sp and "ID DO JOGADOR" in sp


def test_analyze_with_subject_echoes_identification(client):
    r = client.post(
        "/tennis/analyze",
        files={"file": ("clip.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "player_name": "João", "player_outfit": "camiseta azul", "player_side": "fundo",
              "player_handedness": "canhoto", "player_racket_color": "preta", "player_glasses": "true"},
    )
    assert r.status_code == 200, r.text
    subj = r.json()["subject"]
    assert subj and subj["name"] == "João" and subj["outfit"] == "camiseta azul" and subj["side"] == "fundo"
    assert subj["handedness"] == "canhoto" and subj["racket_color"] == "preta" and subj["glasses"] is True


def test_invalid_handedness_is_dropped_not_422(client):
    # valor fora do enum não deve quebrar a construção do SubjectHint (vira None)
    r = client.post(
        "/tennis/analyze",
        files={"file": ("clip.mp4", b"\x00" * 512, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "player_name": "João", "player_handedness": "ambidestro"},
    )
    assert r.status_code == 200, r.text
    subj = r.json()["subject"]
    assert subj and subj.get("handedness") is None


def test_analyze_without_subject_has_null_subject(client):
    r = client.post(
        "/tennis/analyze",
        files={"file": ("clip.mp4", b"\x00" * 512, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false"},
    )
    assert r.status_code == 200
    assert r.json()["subject"] is None


def test_analyze_match_override_forced_to_clip(client):
    # modo PARTIDA removido: pedir mode=match retorna um CLIP (forçado), não erro
    r = client.post(
        "/tennis/analyze",
        files={"file": ("x.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "female", "mode": "match", "with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"]["mode"] == "clip"                 # forçado a clip
    assert body["metrics"]["clip_quality_score"] == 7.4    # veio o _clip_result do mock
    assert body["audio_base64"] is None


def test_clip_too_long_rejected(client, monkeypatch):
    # duração conhecida acima de clip_max_seconds (180s) → 413 (ClipTooLong)
    from app.tennis import service as svc
    monkeypatch.setattr(svc, "probe_duration_seconds", lambda p: cfg.clip_max_seconds + 30)
    r = client.post(
        "/tennis/analyze",
        files={"file": ("longo.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "with_audio": "false"},
    )
    assert r.status_code == 413, r.text


def test_clip_within_limit_ok(client, monkeypatch):
    # duração dentro do teto → segue normal (clip)
    from app.tennis import service as svc
    monkeypatch.setattr(svc, "probe_duration_seconds", lambda p: 30.0)
    r = client.post(
        "/tennis/analyze",
        files={"file": ("curto.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["route"]["mode"] == "clip"


def test_analyze_without_key_returns_503(client, monkeypatch):
    monkeypatch.setattr(cfg, "gemini_api_key", None)
    r = client.post(
        "/tennis/analyze",
        files={"file": ("clip.mp4", b"\x00" * 64, "video/mp4")},
        data={"gender": "male"},
    )
    assert r.status_code == 503


def test_health_endpoint(client):
    r = client.get("/tennis/health")
    assert r.status_code == 200
    body = r.json()
    assert body["analysis_model"] == cfg.analysis_model
    assert "gemini_api_key" not in body       # nunca expõe a chave


def test_frontend_served(client):
    r = client.get("/tennis/")
    assert r.status_code == 200
    assert "BitVar IA" in r.text


# --------------------------------------------------------------------------- #
# histórico & exportação (spec E1) — sem pool, degrada graciosamente           #
# --------------------------------------------------------------------------- #
def test_list_analyses_empty_without_pool(client):
    # DB indisponível nos testes → lista vazia, nunca erro
    r = client.get("/tennis/analyses")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and body["limit"] == 20


def test_get_analysis_404_without_pool(client):
    r = client.get("/tennis/analyses/123")
    assert r.status_code == 404


def test_export_analysis_404_without_pool(client):
    r = client.get("/tennis/analyses/123/export?format=txt")
    assert r.status_code == 404


def test_get_audio_404_without_pool(client):
    r = client.get("/tennis/analyses/123/audio")
    assert r.status_code == 404


def test_render_txt_report_is_readable():
    from app.tennis.router import _render_txt_report
    rec = {
        "id": 7, "gender": "male", "mode": "clip", "created_at": "2026-06-13T00:00:00Z",
        "result_json": {
            "metrics": {"shot_identified": "forehand", "action_phase": "baseline_rally",
                        "clip_quality_score": 7.4, "key_improvement": "Gire mais o quadril.",
                        "secondary_improvements": ["Antecipe o split step."]},
            "narrative": "Você jogou muito bem.",
        },
    }
    txt = _render_txt_report(rec)
    assert "Análise de Tênis #7" in txt
    assert "PRINCIPAL CORREÇÃO" in txt and "Gire mais o quadril." in txt
    assert "RELATÓRIO DO TREINADOR" in txt and "Você jogou muito bem." in txt


def test_subject_provided_detects_new_fields():
    assert SubjectHint().provided() is False
    assert SubjectHint(handedness="canhoto").provided() is True
    assert SubjectHint(glasses=True).provided() is True
    assert SubjectHint(glasses=False).provided() is False   # False não conta como dica
    assert SubjectHint(racket_color="preta").provided() is True


# --------------------------------------------------------------------------- #
# WF1 — classificação FASE→GOLPE (taxonomia, smash proibido, phase_alternative) #
# --------------------------------------------------------------------------- #
def test_clip_prompt_bans_smash_outside_attack():
    # a taxonomia golpe-por-fase e a regra dura precisam chegar ao modelo na chamada 1.
    from app.tennis.prompts import analysis_system_prompt
    sp = analysis_system_prompt("male", "clip")
    low = sp.lower()
    assert "overhead_smash" in sp
    assert "proibido" in low
    assert "attack" in sp and "net_play" in sp
    assert "drop_shot" in sp and "deixadinha" in low
    assert "defesa" in low and "ataque" in low
    assert "saque" in low and "morre" in low
    assert "joelhos" in low
    # o match NÃO recebe essa taxonomia (é estatística, não golpe-por-fase)
    assert "deixadinha" not in analysis_system_prompt("male", "match").lower()


def test_clip_phase_alternative_is_optional():
    # invariante 1: campos novos de fase são Optional com default None.
    c = _clip_result()  # não seta os novos campos de fase alternativa
    assert c.phase_alternative is None
    assert c.phase_alternative_rationale is None
    c2 = ClipAnalysis(
        analysis_mode="clip", gender_profile="male", shot_identified="return",
        action_phase="serve_return", phase_alternative="defense",
        phase_alternative_rationale="bola vem do saque adversario; pode ser defesa.",
        phase_confidence="baixa",
        technical_execution=_clip_result().technical_execution,
        clip_quality_score=5.0, key_improvement="Ajuste o split step na recepcao.",
    )
    assert c2.phase_alternative == "defense"
    assert c2.model_dump()["phase_alternative"] == "defense"


def test_phase_first_correction_no_smash_in_report(client, monkeypatch):
    # DoD WF1 (vídeo 00000201): recepção defensiva com deixadinha NÃO pode sair
    # como 'smash em ataque'. Mock devolve o contrato pós-correção.
    so = lambda n: ScoreObs(score=n, observation="base baixa, joelhos flexionados")  # noqa: E731
    corrected = ClipAnalysis(
        analysis_mode="clip", gender_profile="male", shot_identified="drop_shot",
        action_phase="serve_return", phase_confidence="baixa", shot_confidence="media",
        phase_alternative="defense",
        phase_alternative_rationale="bola vem do saque adversario e ele recua; recepcao, nao ataque.",
        visual_evidence="bola alta vinda do saque adversario; toque curto que morre perto da rede",
        subject_lock_confidence="media", handedness="destro",
        floating_ball_fault=True,
        technical_execution=TechnicalExecution(
            preparation=so(5), contact_point=so(5), follow_through=so(5),
            balance_and_posture=so(4), racket_path=so(5),
        ),
        lower_body_base=LowerBodyBase(defensive_base_flexion=so(3), stability_center_of_gravity=so(3)),
        clip_quality_score=4.5,
        key_improvement="Na recepcao, abaixe a base e devolva profundo em vez da deixadinha.",
        secondary_improvements=["Antecipe o split step na devolucao."],
    )

    class _FakeGeminiPhase(_FakeGemini):
        def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
            return corrected if schema_model is ClipAnalysis else _match_result()

        def narrate(self, metrics, *, gender, mode, player_name=None):
            return "Na recepcao voce leu bem a bola, mas devolva mais profundo."

    monkeypatch.setattr(trouter.service, "gemini", _FakeGeminiPhase())

    r = client.post(
        "/tennis/analyze",
        files={"file": ("00000201.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    m = r.json()["metrics"]
    assert m["action_phase"] in ("defense", "serve_return")
    assert m["shot_identified"] in ("drop_shot", "return")
    assert m["phase_alternative"] == "defense"
    narrative = r.json()["narrative"] or ""
    assert "smash" not in m["key_improvement"].lower()
    assert "smash" not in narrative.lower()
    from app.tennis.router import _render_txt_report
    rec = {"id": 201, "gender": "male", "mode": "clip", "created_at": "2026-06-24T00:00:00Z",
           "result_json": {"metrics": m, "narrative": narrative}}
    txt = _render_txt_report(rec).lower()
    assert "smash" not in txt
    assert "drop_shot" in txt or "deixadinha" in m["key_improvement"].lower()
    # guard-rail REAL (não só a string do mock): o prompt de narrativa desta fase
    # defensiva de fato bane 'smash' e ancora na base — fecha o gap "testa o mock".
    from app.tennis.prompts import build_narrative_prompt
    real_prompt = build_narrative_prompt(m, "male", "clip")
    assert 'NÃO use a palavra "smash"' in real_prompt and "MEMBROS INFERIORES" in real_prompt


# --------------------------------------------------------------------------- #
# WF2 — eixo de NÍVEL (amador|profissional) + gate de regras por categoria      #
# --------------------------------------------------------------------------- #
def test_normalize_level_tolerant():
    assert normalize_level("profissional") == "profissional"
    assert normalize_level("PRO") == "profissional"
    assert normalize_level("amateur") == "amador"
    assert normalize_level(None) == "amador"
    assert normalize_level("") == "amador"
    assert normalize_level("qualquer-coisa") == "amador"  # tolerante, não levanta


def test_applicable_rules_intersection():
    from app.tennis.rules import applicable_rules
    pro = applicable_rules("male", "profissional")
    assert any("PARCEIRO DO SACADOR" in r for r in pro)
    assert applicable_rules("male", "amador") == []
    assert applicable_rules("female", "profissional") == []
    assert applicable_rules("female", "amador") == []


def test_rules_block_gate_by_category():
    from app.tennis.rules import build_rules_block
    assert build_rules_block("male", "amador") is None
    blk = build_rules_block("male", "profissional")
    assert blk is not None
    assert "PARCEIRO DO SACADOR" in blk and "rede" in blk
    assert "APENAS" in blk
    assert build_rules_block("female", "profissional") is None


def test_analysis_prompt_injects_rules_only_for_pro():
    from app.tennis.prompts import analysis_system_prompt
    from app.tennis.rules import build_rules_block
    pro_block = build_rules_block("male", "profissional")
    sp_pro = analysis_system_prompt("male", "clip", None, None, pro_block)
    assert "PARCEIRO DO SACADOR" in sp_pro
    am_block = build_rules_block("male", "amador")
    sp_am = analysis_system_prompt("male", "clip", None, None, am_block)
    assert "PARCEIRO DO SACADOR" not in sp_am
    sp_match = analysis_system_prompt("male", "match", None, None, pro_block)
    assert "PARCEIRO DO SACADOR" in sp_match


def test_build_route_threads_level():
    r = build_route("male", duration=10, override=None, file_size_bytes=5_000_000, level_in="pro")
    assert r.info.level == "profissional"
    r2 = build_route("male", duration=10, override=None, file_size_bytes=5_000_000)
    assert r2.info.level == "amador"


def test_analyze_echoes_level(client):
    r = client.post(
        "/tennis/analyze",
        files={"file": ("clip.mp4", b"\x00" * 1024, "video/mp4")},
        data={"gender": "male", "level": "profissional", "mode": "clip", "with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["route"]["level"] == "profissional"
    r2 = client.post(
        "/tennis/analyze",
        files={"file": ("clip.mp4", b"\x00" * 1024, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false"},
    )
    assert r2.json()["route"]["level"] == "amador"


# --------------------------------------------------------------------------- #
# WF3 — reponderação da nota do CLIP para a base/membros inferiores             #
# --------------------------------------------------------------------------- #
def test_clip_weight_models_sum_to_one():
    from app.tennis.weights import CLIP_WEIGHT_MODELS
    for name, spec in CLIP_WEIGHT_MODELS.items():
        total = round(sum(w for w, _, _ in spec.values()), 6)
        assert total == 1.0, f"{name} soma {total}"


def test_clip_defensive_model_prioritizes_base_over_arm():
    from app.tennis.weights import CLIP_WEIGHT_MODELS, DEFENSIVE_CLIP_MODEL
    spec = CLIP_WEIGHT_MODELS[DEFENSIVE_CLIP_MODEL]
    base_keys = {"defensive_base_flexion", "stability_center_gravity",
                 "floating_ball_control", "split_step", "court_positioning",
                 "recovery_after_shot"}
    arm_keys = {"balance_and_posture", "contact_point", "preparation",
                "follow_through", "racket_path"}
    base_w = sum(w for k, (w, _, _) in spec.items() if k in base_keys)
    arm_w = sum(w for k, (w, _, _) in spec.items() if k in arm_keys)
    # 'base DOMINA' não pode degradar para 'base empata': piso explícito (era só >).
    assert base_w >= 0.70 and arm_w <= 0.25, f"base DOMINA? base={base_w} braço={arm_w}"


def test_clip_phase_routes_to_correct_model():
    from app.tennis.weights import (
        DEFENSIVE_CLIP_MODEL, NEUTRAL_CLIP_MODEL, OFFENSIVE_CLIP_MODEL,
        clip_weight_model_for_phase,
    )
    assert clip_weight_model_for_phase("serve_return") == DEFENSIVE_CLIP_MODEL
    assert clip_weight_model_for_phase("defense") == DEFENSIVE_CLIP_MODEL
    assert clip_weight_model_for_phase("attack") == OFFENSIVE_CLIP_MODEL
    assert clip_weight_model_for_phase("net_play") == OFFENSIVE_CLIP_MODEL
    assert clip_weight_model_for_phase("baseline_rally") == NEUTRAL_CLIP_MODEL
    assert clip_weight_model_for_phase(None) == NEUTRAL_CLIP_MODEL
    assert clip_weight_model_for_phase("xpto") == NEUTRAL_CLIP_MODEL


def test_compute_clip_weighted_score_shape_and_sum():
    from app.tennis.weights import compute_clip_weighted_score
    metrics = {
        "lower_body_base": {
            "defensive_base_flexion": {"score": 8, "observation": "x"},
            "stability_center_of_gravity": {"score": 7, "observation": "x"},
        },
        "footwork_and_movement": {"split_step": {"score": 6, "observation": "x"}},
        "technical_execution": {"contact_point": {"score": 5, "observation": "x"}},
        "floating_ball_fault": False,
    }
    ws = compute_clip_weighted_score(metrics, "serve_return", "male")
    assert set(ws) == {"score", "weighting_model", "component_breakdown",
                       "components_present", "components_total"}
    assert 0 <= ws["score"] <= 100
    assert ws["weighting_model"] == "clip_defensive_base_v1"
    csum = round(sum(c["contribution_pts"] for c in ws["component_breakdown"]), 1)
    assert abs(csum - ws["score"]) < 0.2
    missing = [c for c in ws["component_breakdown"] if not c["present"]]
    assert missing and all(c["contribution_pts"] == 0.0 for c in missing)


def test_floating_ball_fault_lowers_clip_score():
    from app.tennis.weights import compute_clip_weighted_score
    base = {
        "lower_body_base": {"defensive_base_flexion": {"score": 5, "observation": "x"}},
        "footwork_and_movement": {"split_step": {"score": 5, "observation": "x"}},
    }
    clean = compute_clip_weighted_score({**base, "floating_ball_fault": False}, "serve_return")
    fault = compute_clip_weighted_score({**base, "floating_ball_fault": True}, "serve_return")
    assert clean["score"] > fault["score"]


def test_clip_low_base_high_legs_yields_low_defensive_score():
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
        "floating_ball_observation": "base alta + raquete baixa, bola flutuou",
    }
    ws = compute_clip_weighted_score(recepcao_ruim, "serve_return")
    assert ws["score"] < 45, ws["score"]


def test_analyze_clip_has_weighted_score_phase_conditioned(client):
    # ponta-a-ponta: o ramo clip do service atacha a nota oficial calibrada por
    # fase ao metrics, preservando clip_quality_score do VLM.
    r = client.post(
        "/tennis/analyze",
        files={"file": ("clip.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    m = r.json()["metrics"]
    assert m["clip_quality_score"] == 7.4               # referência do VLM preservada
    ws = m["weighted_performance_score"]                # nota oficial Python
    assert 0 <= ws["score"] <= 100
    # _clip_result() tem action_phase='baseline_rally' => modelo neutro
    assert ws["weighting_model"] == "clip_neutral_balanced_v1"


# --------------------------------------------------------------------------- #
# WF4 — leitura tática do ponto (tactical_events + point_outcome_link)          #
# --------------------------------------------------------------------------- #
def test_tactical_event_schema_accepts_relational_events():
    so = lambda n: ScoreObs(score=n, observation="x")  # noqa: E731
    te = TechnicalExecution(preparation=so(7), contact_point=so(7), follow_through=so(7),
                            balance_and_posture=so(7), racket_path=so(7))
    clip = ClipAnalysis(
        analysis_mode="clip", gender_profile="male", shot_identified="drop_shot",
        action_phase="serve_return", technical_execution=te, clip_quality_score=6.0,
        key_improvement="leia o vazio antes de finalizar",
        tactical_events=[
            TacticalEvent(event_type="finta", actor="adversario", approx_timestamp_s=2.1,
                          description="adversário tentou a finta e perdeu o tempo"),
            TacticalEvent(event_type="aproveitamento_deslocamento", actor="alvo",
                          description="alvo explorou a dupla fora de posição"),
            TacticalEvent(event_type="espaco_livre", actor="alvo",
                          description="finalizou no espaço livre"),
        ],
        point_outcome_link="não caiu na finta, aproveitou o deslocamento e finalizou no espaço livre",
    )
    assert clip.tactical_events is not None and len(clip.tactical_events) == 3
    assert clip.tactical_events[0].event_type == "finta"
    assert clip.tactical_events[0].actor == "adversario"
    assert "espaço livre" in clip.point_outcome_link
    bare = ClipAnalysis(analysis_mode="clip", gender_profile="male", shot_identified="forehand",
                        technical_execution=te, clip_quality_score=7.0, key_improvement="ok")
    assert bare.tactical_events is None and bare.point_outcome_link is None
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ClipAnalysis(analysis_mode="clip", gender_profile="male", shot_identified="forehand",
                     technical_execution=te, clip_quality_score=7.0, key_improvement="ok",
                     tactical_events=[TacticalEvent(event_type="outro", description=f"e{i}")
                                      for i in range(6)])


def test_clip_prompt_cites_tactical_catalog():
    from app.tennis.prompts import analysis_system_prompt
    sp = analysis_system_prompt("male", "clip")
    low = sp.lower()
    assert "finta" in low
    assert "espaço livre" in low or "espaco_livre" in low
    assert "deslocamento" in low
    assert "quatro jogadores" in low or "4 jogadores" in low
    assert "tactical_events" in sp and "point_outcome_link" in sp
    assert "finta" not in analysis_system_prompt("female", "match").lower()


def test_narrative_prompt_mentions_tactical_reading():
    from app.tennis.prompts import build_narrative_prompt
    p = build_narrative_prompt({}, "male", "clip")
    assert "tactical_events" in p and "point_outcome_link" in p
    assert "espaço livre" in p.lower()


# --------------------------------------------------------------------------- #
# WF5 — tom do coach (esqueleto 4-tempos + few-shot Juca + anti-smash/hype)     #
# --------------------------------------------------------------------------- #
def test_narrative_prompt_juca_skeleton_fewshot_and_anti_smash():
    from app.tennis.prompts import JUCA_FEWSHOT, build_narrative_prompt
    recepcao = {
        "action_phase": "serve_return",
        "shot_identified": "drop_shot",
        "key_improvement": "Abaixe a base na recepcao: joelhos flexionados.",
        "technical_execution": {"balance_and_posture": {"score": 4,
                                 "observation": "pernas estendidas, raquete baixa"}},
    }
    p = build_narrative_prompt(recepcao, "male", "clip", player_name="Cesar")
    assert "ESTRUTURA OBRIGATÓRIA" in p
    assert "NOMEIE A FASE" in p
    assert "MEMBROS INFERIORES" in p
    assert "Traga a REGRA de contexto" in p
    assert "CORREÇÃO objetiva" in p
    assert p.index("NOMEIE A FASE") < p.index("MEMBROS INFERIORES") < p.index("CORREÇÃO objetiva")
    assert JUCA_FEWSHOT in p
    assert "bola flutuante" in p
    assert "raiz do movimento" in p
    assert 'NÃO use a palavra "smash"' in p
    assert "superlativos vazios" in p and "'belo'" in p and "'excelente'" in p
    assert "Juca" in p and "SEM hype" in p
    assert "Cesar" in p
    ataque = {"action_phase": "attack", "shot_identified": "overhead_smash",
              "key_improvement": "Suba mais cedo para o contato."}
    pa = build_narrative_prompt(ataque, "male", "clip")
    assert 'NÃO use a palavra "smash"' not in pa
    assert "NOMEIE A FASE" in pa and JUCA_FEWSHOT in pa
    assert "superlativos vazios" in pa
    pe = build_narrative_prompt({}, "male", "clip", player_name="João")
    assert "João" in pe and "NOMEIE A FASE" in pe and JUCA_FEWSHOT in pe
    assert 'NÃO use a palavra "smash"' not in pe


# --------------------------------------------------------------------------- #
# WF6 — travar no atleta-alvo e ignorar a quadra ao lado/adjacente             #
# --------------------------------------------------------------------------- #
def test_clip_prompt_ignores_adjacent_court():
    from app.tennis.prompts import analysis_system_prompt, build_camera_block, build_subject_block
    sp = analysis_system_prompt("male", "clip")
    low = sp.lower()
    assert ("quadra ao lado" in low) or ("quadra adjacente" in low) or ("adjacente" in low)
    assert ("ignore" in low) or ("descarte" in low)
    assert "rede" in low and "linha" in low
    assert "início ao fim" in low
    block = build_subject_block(outfit="camiseta azul")
    blow = block.lower()
    assert "adjacente" in blow and "início ao fim" in blow
    assert "reconhecimento facial" in blow
    cam = build_camera_block("central").lower()
    assert "adjacente" in cam and ("ignore" in cam)
    sp2 = analysis_system_prompt("male", "clip", block, build_camera_block("central")).lower()
    assert "adjacente" in sp2 and ("ignore" in sp2 or "descarte" in sp2)


# --------------------------------------------------------------------------- #
# pós-revisão — caminho de PRODUÇÃO do score defensivo + narrativa por modo     #
# --------------------------------------------------------------------------- #
def test_analyze_clip_defensive_phase_weighs_base_e2e(client, monkeypatch):
    # contrato models.py <-> weights.py pela serialização REAL do service
    # (ClipAnalysis.model_dump). Os outros testes de peso usam dicts à mão; um rename
    # de campo passaria batido. Aqui um clipe DEFENSIVO precisa cair no modelo
    # defensivo e a flexão de base entra como componente PRESENTE.
    so = lambda n: ScoreObs(score=n, observation="x")  # noqa: E731
    defensive = ClipAnalysis(
        analysis_mode="clip", gender_profile="male", shot_identified="drop_shot",
        action_phase="serve_return",
        technical_execution=TechnicalExecution(
            preparation=so(5), contact_point=so(8), follow_through=so(5),
            balance_and_posture=so(4), racket_path=so(8),
        ),
        footwork_and_movement=FootworkMovement(
            split_step=so(3), court_positioning=so(4), recovery_after_shot=so(4)
        ),
        lower_body_base=LowerBodyBase(
            defensive_base_flexion=so(3), stability_center_of_gravity=so(3)
        ),
        floating_ball_fault=True,
        clip_quality_score=4.5, key_improvement="Abaixe a base na recepção.",
    )

    class _FakeGeminiDef(_FakeGemini):
        def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
            return defensive if schema_model is ClipAnalysis else _match_result()

    monkeypatch.setattr(trouter.service, "gemini", _FakeGeminiDef())
    r = client.post(
        "/tennis/analyze",
        files={"file": ("00000201.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    ws = r.json()["metrics"]["weighted_performance_score"]
    assert ws["weighting_model"] == "clip_defensive_base_v1"
    present = {c["component"] for c in ws["component_breakdown"] if c["present"]}
    assert "defensive_base_flexion" in present     # eixo dominante REALMENTE entrou
    assert "axis_incomplete" not in ws             # base presente => nota confiável
    # contato/raquete altos (8/8) NÃO salvam a nota: a base manda na fase defensiva
    assert ws["score"] < 55, ws["score"]


# --------------------------------------------------------------------------- #
# QUADRANTE — seleção do atleta-alvo por âncora geométrica (plano 25/06)        #
# --------------------------------------------------------------------------- #
def test_normalize_quadrant_tolerant():
    from app.tennis.quadrants import normalize_quadrant
    assert normalize_quadrant(3) == 3
    assert normalize_quadrant("3") == 3
    assert normalize_quadrant("Q3") == 3
    assert normalize_quadrant(" q4 ") == 4
    assert normalize_quadrant(None) is None
    assert normalize_quadrant("") is None          # vazio → desliga a âncora, não levanta
    assert normalize_quadrant("5") is None          # fora de 1-4
    assert normalize_quadrant(0) is None
    assert normalize_quadrant("abc") is None        # lixo → None (tolerante)


def test_quadrant_frame_side_and_label():
    from app.tennis.quadrants import quadrant_frame_side, quadrant_label
    # 1·2 fundo / 3·4 frente; ímpar=esquerda, par=direita (decisão Caio 25/06)
    assert quadrant_frame_side(1) == "esquerda"
    assert quadrant_frame_side(2) == "direita"
    assert quadrant_frame_side(3) == "esquerda"
    assert quadrant_frame_side(4) == "direita"
    assert quadrant_frame_side(None) is None
    assert quadrant_label(3) == "frente-esquerda"
    assert quadrant_label(1) == "fundo-esquerda"


def test_build_quadrant_block_anchors_target():
    from app.tennis.quadrants import build_quadrant_block
    assert build_quadrant_block(None) is None       # sem quadrante → degrada p/ só-aparência
    blk = build_quadrant_block(3, "camisa e short azul")
    assert "canto inferior-esquerdo" in blk         # Q3 = frente-esquerda do FRAME
    assert "PRECEDÊNCIA" in blk                      # quadrante vence a aparência
    assert "camisa e short azul" in blk             # cor entra como fio de continuidade
    assert "SIGA SOMENTE ELE" in blk
    assert "adjacente" in blk.lower()               # ignora quadra ao lado
    assert "observed_side" in blk                   # alimenta o auto-check de setor
    assert "subject_lock_confidence" in blk         # não troca de jogador: baixa a confiança
    bare = build_quadrant_block(2)                  # sem aparência: bloco existe, sem linha de cor
    assert bare and "canto superior-direito" in bare
    assert "veste" not in bare.lower()


def test_build_camera_block_fundo_is_frame_relative():
    from app.tennis.prompts import build_camera_block
    fundo = build_camera_block("fundo")
    assert fundo and "FUNDO" in fundo
    assert "não inverta" in fundo.lower()           # frame-relativo: SEM flip de quadra
    assert "adjacente" in fundo.lower()
    # a convenção 'central' (legado) AINDA inverte esquerda↔direita
    central = build_camera_block("central")
    assert "DIREITO da quadra" in central or "vice-versa" in central.lower()


def test_analysis_prompt_injects_quadrant_block():
    from app.tennis.prompts import analysis_system_prompt
    from app.tennis.quadrants import build_quadrant_block
    qb = build_quadrant_block(3, "azul")
    sp = analysis_system_prompt("male", "clip", None, None, None, quadrant_block=qb)
    assert "canto inferior-esquerdo" in sp
    # PASSO 2 sempre cita a precedência da âncora geométrica (mesmo sem bloco)
    assert "ÂNCORA GEOMÉTRICA" in analysis_system_prompt("male", "clip")
    # mas o canto específico só aparece quando há quadrante
    assert "canto inferior-esquerdo" not in analysis_system_prompt("male", "clip")


def test_route_info_carries_quadrant_optional():
    from app.tennis.models import RouteInfo
    ri = RouteInfo(gender="male", mode="clip", fps=24, media_resolution="x",
                   thinking_level="high", schema_name="male·clip", mode_detection="clip_only")
    assert ri.target_quadrant is None and ri.target_appearance is None and ri.camera_reference is None


def test_build_route_threads_quadrant():
    r = build_route("male", duration=10, override=None, file_size_bytes=5_000_000,
                    camera_reference="FUNDO", target_quadrant="3", target_appearance="azul")
    assert r.info.target_quadrant == 3
    assert r.info.target_appearance == "azul"
    assert r.info.camera_reference == "fundo"       # normalizado p/ minúsculas
    # quadrante inválido vira None (tolerante), não derruba o roteamento
    r2 = build_route("male", duration=10, override=None, file_size_bytes=5_000_000,
                     target_quadrant="bogus")
    assert r2.info.target_quadrant is None
    # sem quadrante: campos ficam None (retrocompatível)
    r3 = build_route("male", duration=10, override=None, file_size_bytes=5_000_000)
    assert r3.info.target_quadrant is None and r3.info.camera_reference is None


def test_check_target_sector_logic():
    from app.tennis.service import _check_target_sector
    assert _check_target_sector({}, None) is None                                   # sem quadrante
    assert _check_target_sector({"positioning": {"observed_side": "centro"}}, 3) is None  # inconclusivo
    assert _check_target_sector({"positioning": {}}, 3) is None                     # setor ausente
    assert _check_target_sector({}, 3) is None                                      # sem positioning
    assert _check_target_sector({"positioning": {"observed_side": "esquerda"}}, 3) is None  # bateu Q3
    bad = _check_target_sector({"positioning": {"observed_side": "direita"}}, 3)    # Q3 esq × dir
    assert bad and bad["expected_side"] == "esquerda" and bad["observed_side"] == "direita"
    assert "atleta errado" in bad["message"]


def test_analyze_echoes_quadrant_gold_case(client, monkeypatch):
    # CASO-OURO do plano 25/06: alvo no Q3 (frente-esquerda, César azul, câmera de
    # fundo). A IA reporta o atleta começando na ESQUERDA do quadro → bate com Q3 →
    # SEM aviso de setor. E a âncora geométrica de fato chega ao prompt da chamada 1.
    so = lambda n: ScoreObs(score=n, observation="x")  # noqa: E731
    cesar = ClipAnalysis(
        analysis_mode="clip", gender_profile="male", shot_identified="forehand",
        action_phase="attack", subject_lock_confidence="alta",
        positioning=PositioningRead(observed_zone="rede", observed_side="esquerda",
                                    recommended_zone="rede", recommended_side="centro",
                                    rationale="finalizou pela esquerda"),
        technical_execution=TechnicalExecution(preparation=so(7), contact_point=so(7),
            follow_through=so(7), balance_and_posture=so(7), racket_path=so(7)),
        clip_quality_score=7.2, key_improvement="Suba mais cedo para o contato.",
    )

    class _FakeGeminiQuad(_FakeGemini):
        def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
            # a âncora geométrica (Q3 = canto inferior-esquerdo) precisa estar no prompt
            assert "canto inferior-esquerdo" in system_prompt
            assert "camisa e short azul" in system_prompt
            return cesar if schema_model is ClipAnalysis else _match_result()

    monkeypatch.setattr(trouter.service, "gemini", _FakeGeminiQuad())
    r = client.post(
        "/tennis/analyze",
        files={"file": ("cesar.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "camera_position": "fundo", "target_quadrant": "3",
              "target_appearance": "camisa e short azul"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"]["target_quadrant"] == 3
    assert body["route"]["target_appearance"] == "camisa e short azul"
    assert body["route"]["camera_reference"] == "fundo"
    m = body["metrics"]
    assert "target_mismatch" not in m
    assert not any("atleta errado" in w for w in body["warnings"])


def test_analyze_quadrant_mismatch_warns_not_errors(client):
    # Alvo no Q3 (esquerda) mas a IA descreve o atleta começando na DIREITA
    # (_clip_result() padrão tem observed_side='direita') → AVISO 'possível atleta
    # errado', marcado em target_mismatch — NUNCA erro, NUNCA troca de atleta.
    r = client.post(
        "/tennis/analyze",
        files={"file": ("x.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "camera_position": "fundo", "target_quadrant": "3"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    mm = body["metrics"]["target_mismatch"]
    assert mm["expected_side"] == "esquerda" and mm["observed_side"] == "direita"
    assert mm["target_quadrant"] == 3
    assert any("atleta errado" in w for w in body["warnings"])


def test_analyze_quadrant_matching_side_no_mismatch(client):
    # Mesmo _clip_result() (observed_side='direita'), mas alvo no Q4 (direita) → bate.
    r = client.post(
        "/tennis/analyze",
        files={"file": ("x.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "target_quadrant": "4"},
    )
    assert r.status_code == 200, r.text
    assert "target_mismatch" not in r.json()["metrics"]


def test_analyze_without_quadrant_unchanged(client):
    # retrocompat: sem quadrante, nada de mismatch e route sem âncora
    r = client.post(
        "/tennis/analyze",
        files={"file": ("x.mp4", b"\x00" * 512, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"]["target_quadrant"] is None
    assert "target_mismatch" not in body["metrics"]


def test_build_camera_block_central_frame_relative_under_quadrant():
    # review-fix: 'central' inverte por padrão (legado), MAS sob quadrante ativo
    # (frame_relative=True) NÃO inverte — senão contradiz a âncora e o auto-check.
    from app.tennis.prompts import build_camera_block
    flip = build_camera_block("central")
    assert "DIREITO da quadra" in flip                   # legado: court-relative
    frame = build_camera_block("central", frame_relative=True)
    assert "não inverta" in frame.lower() and "RELATIVAS À IMAGEM" in frame
    # fundo é sempre frame-relativo, com ou sem flag
    assert "não inverta" in build_camera_block("fundo").lower()


def test_lateral_camera_frame_relative_under_quadrant():
    # review-fix: lateral SEM quadrante é genérico/vago; COM quadrante vira
    # frame-relativo (lado pela IMAGEM), alinhando com o auto-check de setor.
    from app.tennis.prompts import build_camera_block
    plain = build_camera_block("lateral")
    assert "lateral" in plain.lower()
    fr = build_camera_block("lateral", frame_relative=True)
    assert "RELATIVAS À IMAGEM" in fr and "não inverta" in fr.lower()
    assert "LATERAL" in fr                                # nomeia a câmera correta


def test_subject_block_continuity_only_under_quadrant():
    # review-fix: com quadrante, a aparência deixa de ser SELETOR e vira só fio de
    # continuidade; o 'lado da quadra' (quadra-relativo) é omitido.
    from app.tennis.prompts import build_subject_block
    sel = build_subject_block(outfit="camiseta azul", side="direita")
    assert "Analise SOMENTE este jogador" in sel and "lado da quadra" in sel.lower()
    cont = build_subject_block(outfit="camiseta azul", side="direita", quadrant_active=True)
    assert "camiseta azul" in cont
    assert "CONTINUIDADE" in cont and "QUADRANTE" in cont
    assert "Analise SOMENTE este jogador" not in cont    # não é mais o seletor
    assert "lado da quadra" not in cont.lower()           # lado quadra-relativo omitido
    assert "reconhecimento facial" in cont.lower()        # segue sem biometria


def test_central_camera_with_quadrant_is_frame_relative_no_false_mismatch(client, monkeypatch):
    # review-fix (findings 1/3): central + quadrante NÃO injeta o flip esquerda↔direita
    # no prompt, então o auto-check de setor não dispara falso 'atleta errado'.
    seen = {}
    so = lambda n: ScoreObs(score=n, observation="x")  # noqa: E731
    left = ClipAnalysis(
        analysis_mode="clip", gender_profile="male", shot_identified="forehand",
        action_phase="attack",
        positioning=PositioningRead(observed_zone="rede", observed_side="esquerda"),
        technical_execution=TechnicalExecution(preparation=so(7), contact_point=so(7),
            follow_through=so(7), balance_and_posture=so(7), racket_path=so(7)),
        clip_quality_score=7.0, key_improvement="Suba mais cedo para o contato.",
    )

    class _FG(_FakeGemini):
        def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
            seen["sp"] = system_prompt
            return left if schema_model is ClipAnalysis else _match_result()

    monkeypatch.setattr(trouter.service, "gemini", _FG())
    r = client.post(
        "/tennis/analyze",
        files={"file": ("x.mp4", b"\x00" * 1024, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "camera_position": "central", "target_quadrant": "3"},
    )
    assert r.status_code == 200, r.text
    sp = seen["sp"]
    assert "RELATIVAS À IMAGEM" in sp                     # bloco de câmera frame-relativo
    assert "lado DIREITO da quadra" not in sp             # flip legado suprimido sob quadrante
    assert "target_mismatch" not in r.json()["metrics"]   # Q3 esq × observado esq → confere


def test_quadrant_appearance_falls_back_to_outfit(client, monkeypatch):
    # review-fix: o fio de continuidade vem de target_appearance OU, na falta, da
    # roupa do subject. Asserção mira a LINHA do quadrant block (não a do subject),
    # então remover o 'or subject.outfit' quebra o teste.
    seen = {}

    class _FG(_FakeGemini):
        def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
            seen["sp"] = system_prompt
            return _clip_result() if schema_model is ClipAnalysis else _match_result()

    monkeypatch.setattr(trouter.service, "gemini", _FG())
    r = client.post(
        "/tennis/analyze",
        files={"file": ("x.mp4", b"\x00" * 1024, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "target_quadrant": "3", "player_outfit": "camiseta verde"},  # sem target_appearance
    )
    assert r.status_code == 200, r.text
    assert "veste camiseta verde" in seen["sp"]           # outfit virou o fio do quadrant block


def test_build_route_normalizes_blank_appearance_and_camera():
    r = build_route("male", duration=10, override=None, file_size_bytes=5_000_000,
                    target_appearance="   ", camera_reference="   ", target_quadrant="3")
    assert r.info.target_appearance is None               # vazio/branco → None
    assert r.info.camera_reference is None
    assert r.info.target_quadrant == 3


# --------------------------------------------------------------------------- #
# QUADRANTE × CÂMERA — a numeração depende do ângulo (spec 25/06)              #
# o MESMO canto da tela vira número diferente de fundo × lateral; o parâmetro  #
# do prompt e o auto-check de setor são parametrizados pela câmera.            #
# --------------------------------------------------------------------------- #
def test_normalize_camera_axis():
    from app.tennis.quadrants import normalize_camera_axis
    assert normalize_camera_axis("lateral") == "lateral"
    assert normalize_camera_axis(" LADO ") == "lateral"
    assert normalize_camera_axis("side") == "lateral"
    assert normalize_camera_axis("fundo") == "fundo"
    assert normalize_camera_axis("central") == "fundo"     # central → eixo fundo (rede horizontal)
    assert normalize_camera_axis("atras") == "fundo"
    assert normalize_camera_axis(None) == "fundo"          # default seguro, não levanta
    assert normalize_camera_axis("") == "fundo"
    assert normalize_camera_axis("xyz") == "fundo"
    # 4 POSIÇÕES físicas (uma por lado) → 2 eixos: fundos→fundo, laterais→lateral
    assert normalize_camera_axis("fundo_meu") == "fundo"
    assert normalize_camera_axis("fundo_adv") == "fundo"
    assert normalize_camera_axis("lateral_esq") == "lateral"
    assert normalize_camera_axis("lateral_dir") == "lateral"


def test_mirror_camera_positions_share_axis_and_side():
    # As 2 posições de fundo (e as 2 laterais) compartilham numeração/lado — o lado físico
    # só espelha rótulos, não a grade (relativa ao FRAME). Garante que o auto-check de setor
    # NÃO muda entre o fundo do meu time e o fundo do adversário.
    from app.tennis.quadrants import quadrant_frame_side, quadrant_label
    for q in (1, 2, 3, 4):
        assert quadrant_frame_side(q, "fundo_meu") == quadrant_frame_side(q, "fundo_adv")
        assert quadrant_frame_side(q, "lateral_esq") == quadrant_frame_side(q, "lateral_dir")
        assert quadrant_label(q, "fundo_adv") == quadrant_label(q, "fundo")        # mesmo eixo
        assert quadrant_label(q, "lateral_dir") == quadrant_label(q, "lateral")
    # e o canto inferior-esquerdo segue Q3 nos fundos, Q2 nas laterais
    assert quadrant_frame_side(3, "fundo_adv") == "esquerda"
    assert quadrant_frame_side(2, "lateral_dir") == "esquerda"


def test_build_camera_block_accepts_4_position_vocab():
    # review-fix (front-back/prompt HIGH): build_camera_block casava por igualdade exata e
    # NÃO reconhecia os 4 valores que o front envia (fundo_meu/...), perdendo o bloco
    # frame-relativo no fluxo default e vazando o enum cru. Agora casa por prefixo.
    from app.tennis.prompts import build_camera_block
    for cam in ("fundo_meu", "fundo_adv"):
        blk = build_camera_block(cam)                       # SEM quadrante (fluxo default)
        assert "FUNDO" in blk and "não inverta" in blk.lower()   # frame-relativo preservado
        assert cam not in blk                               # enum cru NÃO vaza
        assert "posição '" not in blk                       # nem o fallback genérico cru
    for cam in ("lateral_esq", "lateral_dir"):
        plain = build_camera_block(cam)
        assert "LATERAL" in plain and cam not in plain
        fr = build_camera_block(cam, frame_relative=True)   # COM quadrante
        assert "RELATIVAS À IMAGEM" in fr and "LATERAL" in fr and cam not in fr


def test_quadrant_numbering_is_camera_dependent():
    # A PROVA do Caio: o MESMO canto da tela vira número diferente conforme a câmera.
    from app.tennis.quadrants import quadrant_frame_side, quadrant_label
    # FUNDO: numeração por LINHAS — ímpar=esquerda, par=direita
    assert [quadrant_frame_side(q, "fundo") for q in (1, 2, 3, 4)] == \
        ["esquerda", "direita", "esquerda", "direita"]
    # LATERAL: numeração por COLUNAS — 1·2 = esquerda, 3·4 = direita (Q2/Q3 trocam de lado!)
    assert [quadrant_frame_side(q, "lateral") for q in (1, 2, 3, 4)] == \
        ["esquerda", "esquerda", "direita", "direita"]
    # canto inferior-esquerdo: Q3 de fundo, Q2 na lateral — mesmo lugar, nº diferente
    assert quadrant_label(3, "fundo") == "frente-esquerda"
    assert quadrant_label(2, "lateral") == "esquerda-perto"
    # sem câmera → eixo fundo (retrocompat dos chamadores antigos)
    assert quadrant_frame_side(2) == quadrant_frame_side(2, "fundo") == "direita"


def test_build_quadrant_block_varies_with_camera():
    # o PARÂMETRO do prompt muda dinamicamente com a câmera selecionada no front
    from app.tennis.quadrants import build_quadrant_block
    fundo3 = build_quadrant_block(3, "azul", "fundo")
    lat3 = build_quadrant_block(3, "azul", "lateral")
    assert "canto inferior-esquerdo" in fundo3 and "HORIZONTAL" in fundo3
    assert "canto superior-direito" in lat3 and "VERTICAL" in lat3   # Q3 lateral = sup-dir
    assert fundo3 != lat3                                            # de fato diverge
    # Q2: superior-direito de fundo × inferior-esquerdo na lateral
    assert "canto superior-direito" in build_quadrant_block(2, camera="fundo")
    assert "canto inferior-esquerdo" in build_quadrant_block(2, camera="lateral")


def test_check_target_sector_is_camera_dependent():
    from app.tennis.service import _check_target_sector
    pos = lambda s: {"positioning": {"observed_side": s}}  # noqa: E731
    # Q3 + observado 'direita': ACUSA de fundo (Q3=esquerda), NÃO na lateral (Q3=direita)
    assert _check_target_sector(pos("direita"), 3, "fundo") is not None
    assert _check_target_sector(pos("direita"), 3, "lateral") is None
    # Q2 + observado 'direita': bate de fundo (Q2=direita), ACUSA na lateral (Q2=esquerda)
    assert _check_target_sector(pos("direita"), 2, "fundo") is None
    bad = _check_target_sector(pos("direita"), 2, "lateral")
    assert bad and bad["expected_side"] == "esquerda"


def test_analyze_lateral_quadrant_no_false_mismatch(client):
    # e2e: câmera LATERAL + Q3 (= direita na lateral) com a IA reportando 'direita'
    # (_clip_result padrão) → SEM mismatch. Em fundo, o MESMO Q3+direita acusaria
    # (test_analyze_quadrant_mismatch_warns_not_errors) — prova a parametrização por câmera.
    r = client.post(
        "/tennis/analyze",
        files={"file": ("x.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "camera_position": "lateral", "target_quadrant": "3"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"]["camera_reference"] == "lateral"
    assert "target_mismatch" not in body["metrics"]


def test_analyze_lateral_quadrant_mismatch_warns(client):
    # e2e espelho: câmera LATERAL + Q2 (= esquerda na lateral) com a IA em 'direita' →
    # AVISA. O mesmo Q2 em fundo (=direita) NÃO acusaria — o eixo lateral inverte a expectativa.
    r = client.post(
        "/tennis/analyze",
        files={"file": ("x.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "male", "mode": "clip", "with_audio": "false",
              "camera_position": "lateral", "target_quadrant": "2"},
    )
    assert r.status_code == 200, r.text
    mm = r.json()["metrics"]["target_mismatch"]
    assert mm["expected_side"] == "esquerda" and mm["observed_side"] == "direita"
    assert mm["target_quadrant"] == 2


def test_narrative_prompt_match_is_statistical_not_point():
    # review #1: a narrativa de PARTIDA não pode herdar o esqueleto de UM ponto
    # (fase do ponto / bola flutuante) nem o few-shot single-point do clipe.
    from app.tennis.prompts import JUCA_FEWSHOT, build_narrative_prompt
    pm = build_narrative_prompt(
        {"serve": {"first_serve_points_won_pct": 68}, "key_improvement": "Suba a 1ª de saque."},
        "male", "match",
    )
    assert "NOMEIE A FASE do ponto" not in pm      # esqueleto de UM ponto fora do match
    assert "bola flutuante" not in pm              # falha single-shot não cabe em estatística
    assert JUCA_FEWSHOT not in pm                  # few-shot single-point só no clipe
    assert "NOMEIE O PADRÃO da partida" in pm and "break points" in pm
    # o clipe, ao contrário, mantém o esqueleto de ponto + few-shot
    pc = build_narrative_prompt({"action_phase": "serve_return"}, "male", "clip")
    assert "NOMEIE A FASE do ponto" in pc and JUCA_FEWSHOT in pc
