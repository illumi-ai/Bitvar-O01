"""Testes do módulo de academia — sem rede (Gemini é mockado). Espelha test_tennis.py."""

import asyncio
import io
import os

# app.settings exige DATABASE_URL no import; o Gemini precisa de chave p/ configured.
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost:5432/x")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastapi import HTTPException, UploadFile  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.academia import gemini as agem  # noqa: E402
from app.academia import router as arouter  # noqa: E402
from app.academia.config import academia_settings as cfg  # noqa: E402
from app.academia.models import AcademiaAnalysis, PontoMelhoria  # noqa: E402
from app.academia.service import AcademiaService, EmptyUpload, UploadTooLarge  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures de resultado da chamada 1 (AcademiaAnalysis)                        #
# --------------------------------------------------------------------------- #
def _clean_result() -> AcademiaAnalysis:
    """Execução boa: veredito adequada, mas com um refinamento (RF-004/RF-008 — o
    lado 'o que melhorar' nunca some, aqui como polimento opcional)."""
    return AcademiaAnalysis(
        exercicio_identificado="puxada alta na polia",
        equipamento="polia alta",
        angulo_camera="lateral direita",
        qualidade_video="boa",
        partes_ocultas=[],
        repeticoes_visiveis=2,
        veredito="adequada",
        confiabilidade="alta",
        pontos_fortes=[
            "lombar apoiada no encosto durante toda a série",
            "amplitude completa na fase concêntrica",
        ],
        pontos_a_melhorar=[
            PontoMelhoria(
                categoria="escapula_ombros",
                observacao="dá para buscar um pouco mais de depressão escapular no pico da contração",
                ajuste="leve o ombro para baixo antes de puxar a barra, travando a escápula",
                timestamp_s=6.0,
                prioridade="refinamento",
            ),
        ],
        feedback_ideal="execução sólida e apta a servir de referência; refine só a "
        "depressão escapular no pico para isolar ainda melhor as costas",
        risco_lesao=False,
        musculos_esperados=["latíssimo do dorso", "bíceps", "romboides"],
        observacoes=None,
    )


def _risco_lesao_result() -> AcademiaAnalysis:
    """Leg press com valgo dinâmico severo — risco de lesão (RF-003)."""
    return AcademiaAnalysis(
        exercicio_identificado="leg press 45 graus",
        equipamento="leg press 45°",
        angulo_camera="frontal",
        qualidade_video="boa",
        partes_ocultas=[],
        repeticoes_visiveis=3,
        veredito="inadequada",
        confiabilidade="alta",
        pontos_fortes=["quadril e lombar apoiados no encosto durante toda a série"],
        pontos_a_melhorar=[
            PontoMelhoria(
                categoria="joelhos",
                observacao="pés mal posicionados na plataforma e joelhos caindo para dentro "
                "(valgo dinâmico) entre 11 e 26 segundos",
                ajuste="plante o pé inteiro na plataforma, afastado na largura dos ombros, "
                "e faça o joelho seguir a direção da ponta do pé na descida e na subida",
                timestamp_s=11.0,
                prioridade="risco_lesao",
            ),
        ],
        feedback_ideal="interrompa a série e ajuste os pés na plataforma antes de continuar; "
        "o apoio do quadril e da lombar já está correto e é para manter",
        risco_lesao=True,
        musculos_esperados=["quadríceps", "glúteos", "isquiotibiais"],
        observacoes=None,
    )


class _FakeGemini:
    def upload_video(self, path, mime_type=None):
        return SimpleNamespace(name="files/x", uri="https://files/x",
                               mime_type="video/mp4", state=SimpleNamespace(name="ACTIVE"))

    def delete_file(self, file):
        pass

    def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
        return _clean_result()

    def narrate(self, metrics, *, student_name=None):
        if metrics.get("risco_lesao"):
            return (
                f"{student_name or 'Você'}, para tudo agora: os joelhos estão caindo para "
                "dentro no leg press. Ajuste os pés antes de continuar."
            )
        return "Essa puxada alta ficou tecnicamente sólida, com lombar apoiada e amplitude completa."

    def synthesize(self, narrative):
        return agem._pcm_to_wav(b"\x00\x01" * 2000, 24000, 1, 2)


class _FakeGeminiRisco(_FakeGemini):
    def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
        return _risco_lesao_result()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(arouter.service, "gemini", _FakeGemini())
    monkeypatch.setattr(cfg, "gemini_api_key", "test-key")
    from app.main import app
    return TestClient(app)


# --------------------------------------------------------------------------- #
# guarda de Content-Length (413 antes de ler o corpo)                          #
# --------------------------------------------------------------------------- #
def _req_with_content_length(value: int) -> Request:
    scope = {"type": "http", "headers": [(b"content-length", str(value).encode())]}
    return Request(scope)


def test_enforce_content_length_rejects_oversize():
    with pytest.raises(HTTPException) as ei:
        arouter._enforce_content_length(_req_with_content_length(cfg.max_upload_bytes + 1))
    assert ei.value.status_code == 413


def test_enforce_content_length_allows_within_limit():
    arouter._enforce_content_length(_req_with_content_length(1024))  # não levanta


# --------------------------------------------------------------------------- #
# guarda de tamanho no streaming (UploadTooLarge / EmptyUpload)                #
# --------------------------------------------------------------------------- #
def test_save_upload_too_large(monkeypatch):
    svc = AcademiaService(cfg)
    monkeypatch.setattr(cfg, "academia_max_upload_mb", 0)  # qualquer byte estoura
    uf = UploadFile(filename="x.mp4", file=io.BytesIO(b"x" * 4096))
    with pytest.raises(UploadTooLarge):
        asyncio.run(svc._save_upload(uf))


def test_save_upload_empty():
    svc = AcademiaService(cfg)
    uf = UploadFile(filename="x.mp4", file=io.BytesIO(b""))
    with pytest.raises(EmptyUpload):
        asyncio.run(svc._save_upload(uf))


def test_safe_suffix_sanitizes():
    from app.academia.service import _safe_suffix
    assert _safe_suffix("clip.mp4") == ".mp4"
    assert _safe_suffix("CLIP.MOV") == ".mov"
    assert _safe_suffix("name.\x00mp4") == ".mp4"      # null-byte saneado
    assert _safe_suffix("noext") == ".mp4"
    assert _safe_suffix("x." + "y" * 50) == ".mp4"      # extensão absurda


def test_save_upload_weird_filename_does_not_raise():
    svc = AcademiaService(cfg)
    uf = UploadFile(filename="bad.\x00mp4", file=io.BytesIO(b"\x00" * 32))
    path, size = asyncio.run(svc._save_upload(uf))
    try:
        assert size == 32 and path.endswith(".mp4") and os.path.exists(path)
    finally:
        os.remove(path)


# --------------------------------------------------------------------------- #
# endpoint /academia/analyze com Gemini mockado                                #
# --------------------------------------------------------------------------- #
def test_analyze_clean_execution_three_outputs(client):
    r = client.post(
        "/academia/analyze",
        files={"file": ("puxada.mp4", b"\x00" * 2048, "video/mp4")},
        data={"student_name": "Marina", "with_audio": "true"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    m = body["metrics"]
    assert body["exercicio"] == "puxada alta na polia"
    assert m["veredito"] == "adequada"
    assert m["risco_lesao"] is False
    # RF-008/RF-004: o lado "o que melhorar" NUNCA some — numa execução boa vem
    # como refinamento (não rebaixa o veredito), e o "o que está bom" está cheio.
    assert m["pontos_fortes"]
    assert m["pontos_a_melhorar"] and m["pontos_a_melhorar"][0]["prioridade"] == "refinamento"
    assert m["feedback_ideal"]
    assert body["narrative"]
    assert body["audio_base64"]                        # saída 3 presente
    assert body["persisted_id"] is None                 # persistência opt-in, off por default


def test_analyze_execution_with_errors_returns_veredicto_e_pontos(client, monkeypatch):
    # execução com ponto moderado (não risco de lesão) => veredito não-adequado
    erro_moderado = AcademiaAnalysis(
        exercicio_identificado="rosca bíceps na polia baixa",
        equipamento="polia baixa",
        angulo_camera="lateral",
        qualidade_video="boa",
        partes_ocultas=[],
        repeticoes_visiveis=4,
        veredito="parcialmente_adequada",
        confiabilidade="alta",
        pontos_fortes=["punho mantido neutro durante toda a execução"],
        pontos_a_melhorar=[
            PontoMelhoria(
                categoria="cotovelos",
                observacao="cotovelo avança à frente do tronco na subida, tirando a tensão do bíceps",
                ajuste="mantenha o cotovelo fixo ao lado do tronco durante toda a rosca",
                timestamp_s=4.2,
                prioridade="moderada",
            ),
        ],
        feedback_ideal="mantenha o cotovelo fixo ao lado do tronco; o resto da execução já está estável",
        risco_lesao=False,
        musculos_esperados=["bíceps braquial"],
        observacoes=None,
    )

    class _FakeGeminiModerado(_FakeGemini):
        def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
            return erro_moderado

    monkeypatch.setattr(arouter.service, "gemini", _FakeGeminiModerado())
    r = client.post(
        "/academia/analyze",
        files={"file": ("rosca.mp4", b"\x00" * 2048, "video/mp4")},
        data={"with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    m = r.json()["metrics"]
    assert m["veredito"] == "parcialmente_adequada"
    assert len(m["pontos_a_melhorar"]) == 1
    p = m["pontos_a_melhorar"][0]
    assert p["categoria"] == "cotovelos"
    assert p["prioridade"] == "moderada"
    # par obrigatório o-que-não-está-ideal (observacao) → como-ajustar (ajuste)
    assert p["observacao"] and p["ajuste"] and p["observacao"] != p["ajuste"]
    assert m["risco_lesao"] is False


def test_analyze_risco_lesao_forces_inadequada(client, monkeypatch):
    # RF-003: valgo dinâmico severo no leg press => veredito inadequada + risco_lesao=True
    monkeypatch.setattr(arouter.service, "gemini", _FakeGeminiRisco())
    r = client.post(
        "/academia/analyze",
        files={"file": ("legpress.mp4", b"\x00" * 2048, "video/mp4")},
        data={"student_name": "Paulinho", "with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    m = body["metrics"]
    assert m["veredito"] == "inadequada"
    assert m["risco_lesao"] is True
    assert any(p["prioridade"] == "risco_lesao" for p in m["pontos_a_melhorar"])
    assert any(p["categoria"] == "joelhos" for p in m["pontos_a_melhorar"])
    # RN-01: o prompt REAL construído a partir dessas métricas impõe a abertura
    # de interrupção (não asserta sobre a string do mock, e sim sobre o sistema)
    from app.academia.prompts import build_narrative_prompt
    p = build_narrative_prompt(m, student_name="Paulinho")
    assert "RISCO DE LESÃO DETECTADO" in p
    assert "INTERROMPER" in p
    assert "Paulinho" in p


def test_analyze_without_key_returns_503(client, monkeypatch):
    monkeypatch.setattr(cfg, "gemini_api_key", None)
    r = client.post(
        "/academia/analyze",
        files={"file": ("clip.mp4", b"\x00" * 64, "video/mp4")},
        data={},
    )
    assert r.status_code == 503


def test_health_endpoint(client):
    r = client.get("/academia/health")
    assert r.status_code == 200
    body = r.json()
    assert body["analysis_model"] == cfg.academia_analysis_model
    assert body["configured"] is True
    assert "gemini_api_key" not in body       # nunca expõe a chave


def test_health_endpoint_reflects_missing_key(client, monkeypatch):
    monkeypatch.setattr(cfg, "gemini_api_key", None)
    r = client.get("/academia/health")
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_frontend_served(client):
    r = client.get("/academia/")
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# histórico & exportação — sem pool, degrada graciosamente                     #
# --------------------------------------------------------------------------- #
def test_list_analyses_empty_without_pool(client):
    r = client.get("/academia/analyses")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and body["limit"] == 20


def test_get_analysis_404_without_pool(client):
    r = client.get("/academia/analyses/123")
    assert r.status_code == 404


def test_export_analysis_404_without_pool(client):
    r = client.get("/academia/analyses/123/export?format=txt")
    assert r.status_code == 404


def test_get_audio_404_without_pool(client):
    r = client.get("/academia/analyses/123/audio")
    assert r.status_code == 404


def test_render_txt_report_is_readable():
    from app.academia.router import _render_txt_report
    rec = {
        "id": 7, "student_name": "Paulinho", "created_at": "2026-07-22T00:00:00Z",
        "result_json": {
            "metrics": {
                "exercicio_identificado": "leg press 45 graus", "veredito": "inadequada",
                "risco_lesao": True,
                "feedback_ideal": "ajuste os pés na plataforma antes de continuar",
                "pontos_a_melhorar": [{"categoria": "joelhos", "prioridade": "risco_lesao",
                           "observacao": "valgo dinâmico severo",
                           "ajuste": "plante o pé inteiro e alinhe o joelho à ponta do pé"}],
                "pontos_fortes": ["lombar apoiada no encosto"],
            },
            "narrative": "Paulinho, para tudo agora: ajuste os pés no leg press.",
        },
    }
    txt = _render_txt_report(rec)
    assert "Análise de Academia #7" in txt
    assert "RISCO DE LESÃO" in txt
    assert "FEEDBACK IDEAL" in txt and "ajuste os pés na plataforma" in txt
    # o que está bom + par o-que-melhorar → como-ajustar no relatório de compartilhamento
    assert "O QUE ESTÁ BOM" in txt and "lombar apoiada no encosto" in txt
    assert "O QUE MELHORAR → COMO AJUSTAR" in txt
    assert "valgo dinâmico severo" in txt
    assert "ajustar: plante o pé inteiro" in txt
    assert "RELATÓRIO DO PERSONAL TRAINER" in txt and "Paulinho" in txt


# --------------------------------------------------------------------------- #
# prompts — calibragem RF-002/RF-003/RF-004/RF-008/RN-01/RN-02/RN-03/RN-05     #
# --------------------------------------------------------------------------- #
def test_analysis_system_prompt_covers_seven_categories():
    from app.academia.prompts import analysis_system_prompt
    sp = analysis_system_prompt("Marina", fps=24).lower()
    for categoria in ["amplitude", "escápula", "tronco", "cervical", "cotovelos",
                      "joelhos", "ritmo"]:
        assert categoria in sp
    assert "marina" in sp


def test_analysis_prompt_requires_observacao_ajuste_pairing():
    """Cada ponto a melhorar tem de vir com o par o-que-não-está-ideal → como-ajustar."""
    from app.academia.prompts import analysis_system_prompt
    sp = analysis_system_prompt(None, fps=24).lower()
    assert "observacao" in sp
    assert "ajuste" in sp
    assert "o que não está ideal" in sp
    assert "como ajustar" in sp


def test_analysis_prompt_guarantees_feedback_never_disappears():
    """RF-004/RF-008: o feedback do que melhorar não some numa execução boa —
    o prompt exige nomear ao menos um ponto (refinamento) e proíbe inflar."""
    from app.academia.prompts import analysis_system_prompt
    sp = analysis_system_prompt(None, fps=24).lower()
    assert "refinamento" in sp
    assert "sempre nomeie ao menos um ponto a melhorar" in sp
    assert "proibido inflar" in sp


def test_narrative_prompt_pairs_observacao_with_ajuste():
    """A narrativa deve exigir citar o ajuste logo após cada ponto a melhorar."""
    from app.academia.prompts import build_narrative_prompt
    metrics = {
        "risco_lesao": False,
        "pontos_a_melhorar": [{"categoria": "cotovelos", "observacao": "cotovelo à frente",
                   "ajuste": "mantenha o cotovelo fixo", "prioridade": "moderada"}],
    }
    prompt = build_narrative_prompt(metrics, student_name=None).lower()
    assert "ajuste" in prompt
    assert "o que melhorar" in prompt
    assert "o que está bom" in prompt


def test_narrative_prompt_rn01_error_before_praise():
    from app.academia.prompts import build_narrative_prompt
    metrics_risco = {"risco_lesao": True,
                     "pontos_a_melhorar": [{"categoria": "joelhos", "prioridade": "risco_lesao"}]}
    p = build_narrative_prompt(metrics_risco, student_name="Paulinho")
    assert "INTERROMPER" in p or "interromper" in p.lower()
    assert "Paulinho" in p

    metrics_limpo = {"risco_lesao": False, "pontos_a_melhorar": []}
    p2 = build_narrative_prompt(metrics_limpo, student_name=None)
    assert "não invente" in p2.lower()


def test_narrative_prompt_refinamento_opens_positive():
    """Execução boa (só refinamento) NÃO obriga abrir com o negativo — abre pelo
    positivo, mas o corpo ainda traz o que refinar (RF-008)."""
    from app.academia.prompts import build_narrative_prompt
    metrics = {"risco_lesao": False,
               "pontos_a_melhorar": [{"categoria": "escapula_ombros", "prioridade": "refinamento"}]}
    p = build_narrative_prompt(metrics, student_name=None)
    assert "Execução boa" in p           # regra de abertura pelo positivo
    assert "vem PRIMEIRO" not in p       # NÃO caiu na abertura de erro-antes-de-elogio


def test_academia_gemini_narrate_smoke_builds_real_prompt():
    """Smoke test do caminho REAL de AcademiaGemini.narrate (sem _FakeGemini):
    stub só no client do SDK. Teria pego o NameError de build_narrative_prompt
    e verifica que o prompt enviado carrega os marcadores RN-01/RN-05."""
    sent = {}

    class _StubModels:
        def generate_content(self, *, model, contents, config):
            sent["model"] = model
            sent["contents"] = contents
            return SimpleNamespace(text="Paulinho, para tudo agora: ajuste os pés.")

    stub_client = SimpleNamespace(models=_StubModels())
    g = agem.AcademiaGemini(client=stub_client)
    metrics = _risco_lesao_result().model_dump()
    out = g.narrate(metrics, student_name="Paulinho")
    assert out == "Paulinho, para tudo agora: ajuste os pés."
    prompt = sent["contents"]
    assert isinstance(prompt, str)
    # RN-01: abertura de interrupção por risco de lesão vem no prompt real
    assert "RISCO DE LESÃO DETECTADO" in prompt and "INTERROMPER" in prompt
    # RN-05 + RN-03: disclaimer educacional e limitações obrigatórias
    low = prompt.lower()
    assert "educacional" in low and "avaliação presencial" in low
    assert "carga" in low and "ativação muscular" in low
    assert "Paulinho" in prompt


def test_narrative_prompt_has_disclaimer_guardrails():
    from app.academia.prompts import build_narrative_prompt
    p = build_narrative_prompt({}, student_name=None)
    low = p.lower()
    assert "educacional" in low and "avaliação presencial" in low
    assert "carga" in low and "ativação muscular" in low
    assert "profissional" in low
