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
from app.academia import scoring  # noqa: E402
from app.academia.config import academia_settings as cfg  # noqa: E402
from app.academia.models import (  # noqa: E402
    AcademiaAnalysis,
    CriterioChecklist,
    ErroTecnico,
    RepeticaoSegmentada,
)
from app.academia.service import AcademiaService, EmptyUpload, UploadTooLarge  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures de resultado da chamada 1 (AcademiaAnalysis)                        #
# --------------------------------------------------------------------------- #
def _clean_result() -> AcademiaAnalysis:
    """Execução correta: sem erros inventados (RF-004), veredito adequada."""
    return AcademiaAnalysis(
        exercicio_identificado="puxada alta na polia",
        equipamento="polia alta",
        angulo_camera="lateral direita",
        qualidade_video="boa",
        partes_ocultas=[],
        repeticoes_visiveis=2,
        veredito="adequada",
        confiabilidade="alta",
        erros=[],
        acertos=[
            "lombar apoiada no encosto durante toda a série",
            "amplitude completa na fase concêntrica",
        ],
        foco_pratico="buscar mais depressão escapular no pico da contração",
        risco_lesao=False,
        musculos_esperados=["latíssimo do dorso", "bíceps", "romboides"],
        observacoes=None,
        checklist=[
            CriterioChecklist(categoria="amplitude", status="adequado", nota_0a10=9.0,
                              observacao="amplitude completa nas duas repetições"),
            CriterioChecklist(categoria="escapula_ombros", status="ajuste_leve", nota_0a10=8.0,
                              observacao="cabe mais depressão escapular no pico"),
            CriterioChecklist(categoria="tronco", status="adequado", nota_0a10=9.5,
                              observacao="sem balanço de tronco"),
            CriterioChecklist(categoria="cervical", status="adequado", nota_0a10=9.0,
                              observacao="pescoço neutro"),
            CriterioChecklist(categoria="cotovelos", status="nao_observavel", nota_0a10=None,
                              observacao="cotovelo direito sai do quadro"),
            CriterioChecklist(categoria="joelhos", status="nao_observavel", nota_0a10=None,
                              observacao="não se aplica claramente ao exercício sentado"),
            CriterioChecklist(categoria="ritmo", status="adequado", nota_0a10=9.0,
                              observacao="excêntrica controlada"),
        ],
        repeticoes=[
            RepeticaoSegmentada(indice=1, completa=True, inicio_s=1.0, transicao_s=2.5, fim_s=4.0),
            RepeticaoSegmentada(indice=2, completa=True, inicio_s=5.0, transicao_s=6.5, fim_s=8.0,
                                observacao="ritmo idêntico à primeira"),
        ],
        consistencia_amplitude="consistente",
        consistencia_ritmo="consistente",
        observacao_movimento="padrão cíclico estável nas duas repetições",
        corpo_inteiro_visivel=True,
        camera_estavel=True,
        iluminacao_adequada=True,
        recomendacoes_gravacao=[],
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
        erros=[
            ErroTecnico(
                categoria="joelhos",
                descricao="pés mal posicionados na plataforma e joelhos caindo para dentro "
                "(valgo dinâmico) entre 11 e 26 segundos",
                correcao="plante o pé inteiro na plataforma, afastado na largura dos ombros, "
                "e faça o joelho seguir a direção da ponta do pé na descida e na subida",
                timestamp_s=11.0,
                gravidade="risco_lesao",
            ),
        ],
        acertos=["quadril e lombar apoiados no encosto durante toda a série"],
        foco_pratico="ajustar o posicionamento dos pés na plataforma antes de continuar a série",
        risco_lesao=True,
        musculos_esperados=["quadríceps", "glúteos", "isquiotibiais"],
        observacoes=None,
        checklist=[
            CriterioChecklist(categoria="amplitude", status="adequado", nota_0a10=8.0,
                              observacao="amplitude razoável nas repetições visíveis"),
            CriterioChecklist(categoria="escapula_ombros", status="nao_observavel", nota_0a10=None,
                              observacao="não relevante/visível neste ângulo"),
            CriterioChecklist(categoria="tronco", status="adequado", nota_0a10=9.0,
                              observacao="quadril e lombar apoiados no encosto"),
            CriterioChecklist(categoria="cervical", status="nao_observavel", nota_0a10=None,
                              observacao="cabeça parcialmente fora do quadro"),
            CriterioChecklist(categoria="cotovelos", status="nao_observavel", nota_0a10=None,
                              observacao="não se aplica ao leg press"),
            CriterioChecklist(categoria="joelhos", status="a_corrigir", nota_0a10=2.0,
                              observacao="valgo dinâmico severo com pés mal posicionados"),
            CriterioChecklist(categoria="ritmo", status="adequado", nota_0a10=8.0,
                              observacao="descida controlada apesar do valgo"),
        ],
        repeticoes=[
            RepeticaoSegmentada(indice=1, completa=True, inicio_s=2.0, fim_s=9.0),
            RepeticaoSegmentada(indice=2, completa=True, inicio_s=11.0, fim_s=18.0,
                                observacao="valgo mais acentuado"),
            RepeticaoSegmentada(indice=3, completa=True, inicio_s=19.0, fim_s=26.0),
        ],
        consistencia_amplitude="consistente",
        consistencia_ritmo="consistente",
        observacao_movimento="valgo piora ao longo da série",
        corpo_inteiro_visivel=True,
        camera_estavel=True,
        iluminacao_adequada=True,
        recomendacoes_gravacao=[],
    )


class _FakeGemini:
    def __init__(self):
        self.narrate_payloads = []          # captura o dict passado à chamada 2

    def upload_video(self, path, mime_type=None):
        return SimpleNamespace(name="files/x", uri="https://files/x",
                               mime_type="video/mp4", state=SimpleNamespace(name="ACTIVE"))

    def delete_file(self, file):
        pass

    def analyze(self, file, *, schema_model, system_prompt, fps, media_resolution):
        return _clean_result()

    def narrate(self, metrics, *, student_name=None):
        self.narrate_payloads.append(metrics)
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
    assert body["exercicio"] == "puxada alta na polia"
    assert body["metrics"]["veredito"] == "adequada"
    assert body["metrics"]["erros"] == []              # RF-004: sem erro inventado
    assert body["metrics"]["risco_lesao"] is False
    assert body["narrative"]
    assert body["audio_base64"]                        # saída 3 presente
    assert body["persisted_id"] is None                 # persistência opt-in, off por default
    # parâmetros reintroduzidos: checklist completo + nota determinística válida
    assert len(body["metrics"]["checklist"]) == 7
    nota = body["nota_execucao"]
    assert nota["valida"] is True and nota["nota"] is not None
    assert nota["criterios_presentes"] == 5             # 2 categorias nao_observavel saem
    # sem teto: veredito adequada e sem risco
    assert nota.get("teto_aplicado") is None
    # as contribuições somam a nota (renormalização sobre o observável)
    soma = sum(c["contribuicao_pontos"] for c in nota["componentes"])
    assert abs(soma - nota["nota"]) < 0.5
    assert len(body["metrics"]["repeticoes"]) == 2
    assert body["metrics"]["consistencia_ritmo"] == "consistente"
    # a chamada 2 (narrativa) recebe a nota junto das métricas (guard-rail no prompt)
    payload = arouter.service.gemini.narrate_payloads[0]
    assert payload["nota_execucao"]["nota"] == nota["nota"]


def test_analyze_execution_with_errors_returns_veredicto_and_erros(client, monkeypatch):
    # execução com erro moderado (não risco de lesão) => erros preenchidos, veredito não-adequado
    erro_moderado = AcademiaAnalysis(
        exercicio_identificado="rosca bíceps na polia baixa",
        equipamento="polia baixa",
        angulo_camera="lateral",
        qualidade_video="boa",
        partes_ocultas=[],
        repeticoes_visiveis=4,
        veredito="parcialmente_adequada",
        confiabilidade="alta",
        erros=[
            ErroTecnico(
                categoria="cotovelos",
                descricao="cotovelo avança à frente do tronco na subida, tirando a tensão do bíceps",
                correcao="mantenha o cotovelo fixo ao lado do tronco durante toda a rosca",
                timestamp_s=4.2,
                gravidade="moderada",
            ),
        ],
        acertos=["punho mantido neutro durante toda a execução"],
        foco_pratico="mantenha o cotovelo fixo ao lado do tronco durante toda a rosca",
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
    body = r.json()
    m = body["metrics"]
    assert m["veredito"] == "parcialmente_adequada"
    assert len(m["erros"]) == 1
    assert m["erros"][0]["categoria"] == "cotovelos"
    # par obrigatório o-que-está-errado (descricao) → o-que-consertar (correcao)
    assert m["erros"][0]["descricao"]
    assert m["erros"][0]["correcao"]
    assert m["erros"][0]["descricao"] != m["erros"][0]["correcao"]
    assert m["risco_lesao"] is False
    # caminho legado (VLM sem checklist): harmonização completa as 7 categorias,
    # a categoria com erro vira a_corrigir e a nota é bloqueada (pouca cobertura)
    assert len(m["checklist"]) == 7
    por_cat = {c["categoria"]: c for c in m["checklist"]}
    assert por_cat["cotovelos"]["status"] == "a_corrigir"
    assert body["nota_execucao"]["valida"] is False
    assert body["nota_execucao"]["nota"] is None


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
    assert any(e["gravidade"] == "risco_lesao" for e in m["erros"])
    assert any(e["categoria"] == "joelhos" for e in m["erros"])
    # nota coerente com o risco: joelhos zerado no cálculo + teto de 39
    nota = body["nota_execucao"]
    joelhos = next(c for c in nota["componentes"] if c["categoria"] == "joelhos")
    assert joelhos["normalizado"] == 0.0
    assert nota["teto_aplicado"] == 39.0 and nota["nota"] == 39.0
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
                "risco_lesao": True, "foco_pratico": "ajustar os pés na plataforma",
                "erros": [{"categoria": "joelhos", "gravidade": "risco_lesao",
                           "descricao": "valgo dinâmico severo",
                           "correcao": "plante o pé inteiro e alinhe o joelho à ponta do pé"}],
                "acertos": ["lombar apoiada no encosto"],
            },
            "narrative": "Paulinho, para tudo agora: ajuste os pés no leg press.",
        },
    }
    txt = _render_txt_report(rec)
    assert "Análise de Academia #7" in txt
    assert "RISCO DE LESÃO" in txt
    assert "FOCO PRÁTICO PRINCIPAL" in txt and "ajustar os pés na plataforma" in txt
    # par erro→correção no relatório de compartilhamento
    assert "O QUE ESTÁ ERRADO → COMO CONSERTAR" in txt
    assert "valgo dinâmico severo" in txt
    assert "corrigir: plante o pé inteiro" in txt
    assert "RELATÓRIO DO PERSONAL TRAINER" in txt and "Paulinho" in txt


# --------------------------------------------------------------------------- #
# prompts — calibragem RF-002/RF-003/RF-004/RN-01/RN-02/RN-03/RN-05            #
# --------------------------------------------------------------------------- #
def test_analysis_system_prompt_covers_seven_categories():
    from app.academia.prompts import analysis_system_prompt
    sp = analysis_system_prompt("Marina", fps=24).lower()
    for categoria in ["amplitude", "escápula", "tronco", "cervical", "cotovelos",
                      "joelhos", "ritmo"]:
        assert categoria in sp
    assert "marina" in sp


def test_analysis_prompt_requires_erro_correcao_pairing():
    """Cada erro tem de vir com o par o-que-está-errado → o-que-consertar."""
    from app.academia.prompts import analysis_system_prompt
    sp = analysis_system_prompt(None, fps=24).lower()
    assert "correcao" in sp
    assert "o que está errado" in sp
    assert "o que consertar" in sp


def test_narrative_prompt_pairs_erro_with_correcao():
    """A narrativa deve exigir citar o conserto logo após cada erro."""
    from app.academia.prompts import build_narrative_prompt
    metrics = {
        "risco_lesao": False,
        "erros": [{"categoria": "cotovelos", "descricao": "cotovelo à frente",
                   "correcao": "mantenha o cotovelo fixo", "gravidade": "moderada"}],
    }
    prompt = build_narrative_prompt(metrics, student_name=None).lower()
    assert "correcao" in prompt
    assert "conserto" in prompt


def test_narrative_prompt_rn01_error_before_praise():
    from app.academia.prompts import build_narrative_prompt
    metrics_risco = {"risco_lesao": True, "erros": [{"categoria": "joelhos"}]}
    p = build_narrative_prompt(metrics_risco, student_name="Paulinho")
    assert "INTERROMPER" in p or "interromper" in p.lower()
    assert "Paulinho" in p

    metrics_limpo = {"risco_lesao": False, "erros": []}
    p2 = build_narrative_prompt(metrics_limpo, student_name=None)
    assert "não invente" in p2.lower()


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


def test_analysis_prompt_covers_reintroduced_parameters():
    """Chamada 1 instrui checklist das 7 categorias, repetições e captura extra."""
    from app.academia.prompts import analysis_system_prompt
    sp = analysis_system_prompt(None, fps=24)
    low = sp.lower()
    assert "checklist" in low and "nao_observavel" in low and "ajuste_leve" in low
    assert "nota_0a10" in low                     # rubrica 0..10 por categoria
    assert "repeticoes" in low and "transicao_s" in low
    assert "consistencia_amplitude" in low and "consistencia_ritmo" in low
    assert "recomendacoes_gravacao" in low and "camera_estavel" in low
    # o VLM nunca agrega a nota — aritmética fica em Python
    assert "calculada fora, em código" in low


def test_narrative_prompt_mentions_nota_guardrail():
    from app.academia.prompts import build_narrative_prompt
    p = build_narrative_prompt({"erros": [], "risco_lesao": False}, student_name=None)
    assert "nota_execucao" in p
    assert "NÃO invente um número" in p or "não invente um número" in p.lower()


# --------------------------------------------------------------------------- #
# scoring — nota determinística (Python, nunca o VLM)                          #
# --------------------------------------------------------------------------- #
def _analysis(**over) -> AcademiaAnalysis:
    base = dict(
        exercicio_identificado="exercício x", angulo_camera="lateral",
        qualidade_video="boa", veredito="adequada", confiabilidade="alta",
        foco_pratico="foco", risco_lesao=False,
    )
    base.update(over)
    return AcademiaAnalysis(**base)


def _checklist_full(status="adequado", nota=10.0):
    return [CriterioChecklist(categoria=c, status=status, nota_0a10=nota, observacao="ok")
            for c in scoring.CATEGORIAS]


def test_score_all_perfect_is_100():
    a = _analysis(checklist=_checklist_full())
    n = scoring.compute_nota_execucao(a)
    assert n.valida is True and n.nota == 100.0
    assert n.criterios_presentes == 7 and n.cobertura == 1.0
    assert abs(sum(c.contribuicao_pontos for c in n.componentes) - 100.0) < 0.01


def test_score_renormalizes_over_observable():
    """nao_observavel sai do cálculo (nunca vira zero) e os pesos renormalizam."""
    chk = _checklist_full()
    for item in chk[:3]:
        item.status, item.nota_0a10 = "nao_observavel", None
    n = scoring.compute_nota_execucao(_analysis(checklist=chk))
    assert n.valida is True and n.nota == 100.0     # os observáveis são todos 10/10
    assert n.criterios_presentes == 4
    assert "renormalizada" in n.observacao


def test_score_blocked_with_few_criteria():
    chk = _checklist_full()
    for item in chk[:5]:
        item.status, item.nota_0a10 = "nao_observavel", None
    n = scoring.compute_nota_execucao(_analysis(checklist=chk))
    assert n.valida is False and n.nota is None
    assert "bloqueada" in n.observacao


def test_score_blocked_on_bad_video_quality():
    n = scoring.compute_nota_execucao(_analysis(qualidade_video="ruim", checklist=_checklist_full()))
    assert n.valida is False and n.nota is None


def test_score_fallback_when_nota_missing():
    """Status sem nota usa o fallback do módulo original (adequado=0.85)."""
    chk = _checklist_full()
    chk[0].nota_0a10 = None
    n = scoring.compute_nota_execucao(_analysis(checklist=chk))
    comp = next(c for c in n.componentes if c.categoria == chk[0].categoria)
    assert comp.normalizado == 0.85


def test_score_caps_by_veredito():
    """Coerência: nota nunca contradiz o veredito (parcialmente_adequada ≤ 79)."""
    a = _analysis(veredito="parcialmente_adequada", checklist=_checklist_full())
    n = scoring.compute_nota_execucao(a)
    assert n.nota == 79.0 and n.teto_aplicado == 79.0


# --------------------------------------------------------------------------- #
# harmonização — regras de calibragem em código                                #
# --------------------------------------------------------------------------- #
def test_harmonize_fills_missing_checklist_without_warning():
    """Caminho legado (sem checklist): completa as 7 em silêncio."""
    fixed, avisos = scoring.harmonize_analysis(_analysis())
    assert len(fixed.checklist) == 7
    assert all(c.status == "nao_observavel" for c in fixed.checklist)
    assert avisos == []


def test_harmonize_enforces_rf003_in_code():
    """RF-003 vira código: erro risco_lesao força inadequada + risco_lesao=True."""
    a = _analysis(
        veredito="adequada", risco_lesao=False,
        erros=[ErroTecnico(categoria="joelhos", descricao="valgo severo",
                           correcao="alinhe o joelho à ponta do pé", gravidade="risco_lesao")],
    )
    fixed, avisos = scoring.harmonize_analysis(a)
    assert fixed.veredito == "inadequada" and fixed.risco_lesao is True
    assert len(avisos) >= 2 and all("consistência" in w for w in avisos)


def test_harmonize_category_with_error_becomes_a_corrigir():
    chk = _checklist_full()                          # tudo adequado 10/10
    a = _analysis(
        checklist=chk,
        erros=[ErroTecnico(categoria="cotovelos", descricao="cotovelo à frente",
                           correcao="cotovelo fixo ao tronco", gravidade="moderada")],
    )
    fixed, avisos = scoring.harmonize_analysis(a)
    item = next(c for c in fixed.checklist if c.categoria == "cotovelos")
    assert item.status == "a_corrigir"
    assert item.nota_0a10 == 6.0                      # teto por gravidade moderada
    assert any("a_corrigir" in w for w in avisos)


def test_harmonize_risco_flag_without_risco_error_forces_inadequada():
    """Direção inversa de RF-003: flag de risco sem erro grave — conservador,
    o veredito acompanha a flag (não apagamos sinal de segurança do modelo)."""
    fixed, avisos = scoring.harmonize_analysis(_analysis(veredito="adequada", risco_lesao=True))
    assert fixed.veredito == "inadequada" and fixed.risco_lesao is True
    assert any("risco_lesao=true sem erro" in w for w in avisos)


def test_harmonize_a_corrigir_without_error_downgraded():
    """Invariante a_corrigir ⟺ erro na categoria (RF-004): sem erro registrado,
    o item rebaixa para ajuste_leve com nota limitada à banda leve."""
    chk = _checklist_full()
    chk[5].status = "a_corrigir"            # joelhos: a_corrigir com nota 10 e erros=[]
    fixed, avisos = scoring.harmonize_analysis(_analysis(checklist=chk))
    item = next(c for c in fixed.checklist if c.categoria == "joelhos")
    assert item.status == "ajuste_leve"
    assert item.nota_0a10 == 8.0
    assert any("rebaixado para 'ajuste_leve'" in w for w in avisos)


def test_harmonize_two_moderate_errors_never_adequada():
    a = _analysis(
        veredito="adequada",
        erros=[
            ErroTecnico(categoria="cotovelos", descricao="d1", correcao="c1", gravidade="moderada"),
            ErroTecnico(categoria="ritmo", descricao="d2", correcao="c2", gravidade="moderada"),
        ],
    )
    fixed, _ = scoring.harmonize_analysis(a)
    assert fixed.veredito == "parcialmente_adequada"


def test_persist_roundtrip_nota_key_reaches_txt_export(client, monkeypatch):
    """Trava os DOIS lados do contrato persistido: o dict que _maybe_persist grava
    (chaves metrics/nota_execucao/narrative) é o mesmo que _render_txt_report lê."""
    from app.academia import store

    captured = {}

    def fake_save(student_name, result_json, audio_wav=None):
        captured["result_json"] = result_json
        return 42

    monkeypatch.setattr(store, "save", fake_save)
    r = client.post(
        "/academia/analyze",
        files={"file": ("puxada.mp4", b"\x00" * 2048, "video/mp4")},
        data={"with_audio": "false", "persist": "true"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["persisted_id"] == "42"
    rj = captured["result_json"]
    assert set(rj) >= {"metrics", "nota_execucao", "narrative"}
    txt = arouter._render_txt_report(
        {"id": 42, "student_name": None, "created_at": "2026-07-22", "result_json": rj}
    )
    assert "NOTA DE EXECUÇÃO" in txt and "CHECKLIST DAS 7 CATEGORIAS" in txt


# --------------------------------------------------------------------------- #
# frames dos erros — print do momento exato (ffmpeg mockado)                   #
# --------------------------------------------------------------------------- #
def test_frames_skipped_without_errors_or_timestamps():
    from app.academia.frames import extract_error_frames
    assert extract_error_frames("/tmp/x.mp4", []) == ([], [])
    sem_ts = [ErroTecnico(categoria="ritmo", descricao="d", correcao="c",
                          gravidade="leve", timestamp_s=None)]
    assert extract_error_frames("/tmp/x.mp4", sem_ts) == ([], [])


def test_frames_disabled_by_config(monkeypatch):
    from app.academia import frames as fr
    monkeypatch.setattr(cfg, "academia_frames_enabled", False)
    erros = [ErroTecnico(categoria="joelhos", descricao="d", correcao="c",
                         gravidade="risco_lesao", timestamp_s=5.0)]
    assert fr.extract_error_frames("/tmp/x.mp4", erros, cfg) == ([], [])


def test_frames_extracted_in_original_order_with_index(monkeypatch):
    from app.academia import frames as fr
    monkeypatch.setattr(fr, "_grab_frame", lambda path, ts, cfg: b"\xff\xd8fake-jpeg")
    erros = [
        ErroTecnico(categoria="ritmo", descricao="d", correcao="c",
                    gravidade="leve", timestamp_s=None),        # sem timestamp: pulado
        ErroTecnico(categoria="joelhos", descricao="d", correcao="c",
                    gravidade="risco_lesao", timestamp_s=11.0),
        ErroTecnico(categoria="tronco", descricao="d", correcao="c",
                    gravidade="moderada", timestamp_s=3.5),
    ]
    frames, warns = fr.extract_error_frames("/tmp/x.mp4", erros, cfg)
    assert warns == []
    assert [(f.erro_index, f.categoria, f.timestamp_s) for f in frames] == [
        (1, "joelhos", 11.0), (2, "tronco", 3.5),
    ]
    import base64 as b64
    assert b64.b64decode(frames[0].image_base64).startswith(b"\xff\xd8")


def test_frames_cap_and_failure_become_warnings(monkeypatch):
    from app.academia import frames as fr
    calls = {"n": 0}

    def flaky(path, ts, cfg_):
        calls["n"] += 1
        if ts == 2.0:
            raise RuntimeError("ffmpeg falhou")
        return b"\xff\xd8ok"

    monkeypatch.setattr(fr, "_grab_frame", flaky)
    monkeypatch.setattr(cfg, "academia_frames_max", 2)
    erros = [ErroTecnico(categoria="ritmo", descricao="d", correcao="c",
                         gravidade="leve", timestamp_s=float(t)) for t in (1, 2, 3)]
    frames, warns = fr.extract_error_frames("/tmp/x.mp4", erros, cfg)
    assert calls["n"] == 2                      # teto respeitado (3º erro nem tenta)
    assert len(frames) == 1 and frames[0].erro_index == 0
    assert any("limitado a 2" in w for w in warns)
    assert any("indisponível" in w for w in warns)


def test_analyze_risco_returns_error_frame(client, monkeypatch):
    """Endpoint: erro com timestamp gera print no response (frames_erros)."""
    from app.academia import service as asvc
    from app.academia.models import FrameErro

    def fake_extract(path, erros, cfg_):
        assert any(e.timestamp_s == 11.0 for e in erros)
        return [FrameErro(erro_index=0, categoria="joelhos", timestamp_s=11.0,
                          image_base64="ZmFrZQ==")], []

    monkeypatch.setattr(asvc, "extract_error_frames", fake_extract)
    monkeypatch.setattr(arouter.service, "gemini", _FakeGeminiRisco())
    r = client.post(
        "/academia/analyze",
        files={"file": ("legpress.mp4", b"\x00" * 2048, "video/mp4")},
        data={"with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["frames_erros"]) == 1
    f = body["frames_erros"][0]
    assert f["erro_index"] == 0 and f["timestamp_s"] == 11.0
    assert f["mime"] == "image/jpeg" and f["image_base64"] == "ZmFrZQ=="


def test_analyze_clean_execution_has_no_frames(client):
    """Execução limpa (sem erros) não gera print nenhum."""
    r = client.post(
        "/academia/analyze",
        files={"file": ("puxada.mp4", b"\x00" * 2048, "video/mp4")},
        data={"with_audio": "false"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["frames_erros"] == []


def test_render_txt_report_covers_reintroduced_parameters():
    from app.academia.router import _render_txt_report
    rec = {
        "id": 9, "student_name": "Marina", "created_at": "2026-07-22T00:00:00Z",
        "result_json": {
            "metrics": {
                "exercicio_identificado": "puxada alta na polia", "veredito": "adequada",
                "risco_lesao": False, "foco_pratico": "mais depressão escapular",
                "erros": [], "acertos": ["lombar apoiada"],
                "checklist": [{"categoria": "amplitude", "status": "adequado",
                               "nota_0a10": 9.0, "observacao": "amplitude completa"}],
                "repeticoes": [{"indice": 1, "completa": True, "inicio_s": 1.0, "fim_s": 4.0}],
                "angulo_camera": "lateral", "qualidade_video": "boa",
                "corpo_inteiro_visivel": True, "camera_estavel": True,
                "recomendacoes_gravacao": ["filme de lado com os pés no quadro"],
                "confiabilidade": "alta",
            },
            "nota_execucao": {"nota": 91.4, "valida": True, "criterios_presentes": 5,
                              "criterios_totais": 7, "observacao": "nota parcial"},
            "narrative": "Marina, execução sólida.",
        },
    }
    txt = _render_txt_report(rec)
    assert "NOTA DE EXECUÇÃO: 91.4/100" in txt
    assert "CHECKLIST DAS 7 CATEGORIAS" in txt and "amplitude: adequado · 9.0/10" in txt
    assert "REPETIÇÕES SEGMENTADAS" in txt and "rep 1: completa (1s→4s)" in txt
    assert "CAPTURA" in txt and "corpo inteiro: sim" in txt
    assert "para filmar melhor: filme de lado" in txt
