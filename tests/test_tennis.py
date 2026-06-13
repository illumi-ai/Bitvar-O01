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
    Biomechanics, ClipAnalysis, FootworkMovement, MatchAnalysis, OutcomeQuality,
    PositioningRead, PressurePoints, RallyStats, ReturnStats, ScoreObs, ServeStats,
    SubjectHint, TacticalIntent, TechnicalExecution,
)
from app.tennis.routing import (  # noqa: E402
    build_route, decide_mode, normalize_gender, normalize_mode_override,
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


def test_decide_mode_threshold_and_override():
    assert decide_mode(30, None, None)[0] == "clip"
    assert decide_mode(75, None, None)[0] == "match"      # >= limiar
    assert decide_mode(74.9, None, None)[0] == "clip"
    assert decide_mode(600, "clip", None)[0] == "clip"     # override vence a duração
    assert decide_mode(None, None, 10 * 1024 * 1024)[0] == "clip"   # heurística
    assert decide_mode(None, None, 300 * 1024 * 1024)[0] == "match"
    assert decide_mode(None, None, None)[0] == "clip"      # default


def test_mode_override_normalization():
    assert normalize_mode_override("auto") is None
    assert normalize_mode_override(None) is None
    assert normalize_mode_override("MATCH") == "match"
    with pytest.raises(ValueError):
        normalize_mode_override("bogus")


def test_build_route_clip_and_match():
    clip = build_route("male", duration=10, override=None, file_size_bytes=5_000_000)
    assert clip.info.mode == "clip" and clip.info.fps == cfg.clip_fps
    assert clip.info.media_resolution == cfg.clip_media_resolution
    assert clip.schema_model is ClipAnalysis and clip.weight_model is None

    match = build_route("female", duration=600, override=None, file_size_bytes=3 * 10**8)
    assert match.info.mode == "match" and match.info.fps == cfg.match_fps
    assert match.schema_model is MatchAnalysis
    assert match.weight_model == FEMALE_MODEL


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


def test_analyze_match_has_weighted_score(client):
    r = client.post(
        "/tennis/analyze",
        files={"file": ("match.mp4", b"\x00" * 2048, "video/mp4")},
        data={"gender": "female", "mode": "match", "with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"]["mode"] == "match"
    assert body["route"]["weight_model"] == FEMALE_MODEL
    ws = body["metrics"]["weighted_performance_score"]
    assert 0 <= ws["score"] <= 100
    assert ws["weighting_model"] == FEMALE_MODEL
    assert "return" in body["metrics"]        # alias preservado
    assert body["benchmarks"]["second_serve_points_won_pct"] == 50.0
    assert body["audio_base64"] is None       # áudio desligado


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
