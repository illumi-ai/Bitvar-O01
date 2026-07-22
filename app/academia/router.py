"""Endpoints HTTP e exportação da POC de Academia."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from app.events import catalog, emit

from . import store
from .audio import audio_tools_available
from .config import academia_settings as cfg
from .gemini import GeminiError
from .models import (
    AcademiaAnalysisResponse,
    AcademiaDeleteResponse,
    AcademiaHealthResponse,
    AcademiaHistoryResponse,
    AcademiaStoredAnalysis,
    PractitionerHint,
    TargetDescriptionTranscriptionResponse,
)
from .profiles import list_profiles
from .service import (
    AcademiaService,
    AnalysisBusy,
    EmptyVoiceAudio,
    EmptyUpload,
    InvalidVoiceAudio,
    InvalidVideo,
    NoSpeechDetected,
    UnverifiableVideoDuration,
    UploadTooLarge,
    VideoTooLong,
    VoiceAudioTooLarge,
    VoiceAudioTooLong,
    VoiceTranscriptionBusy,
    VoiceTranscriptionUnavailable,
)

router = APIRouter(prefix="/academia", tags=["academia"])
service = AcademiaService(cfg)

_FRONTEND = Path(__file__).resolve().parent.parent / "static" / "academia" / "index.html"
_history_bearer = HTTPBearer(auto_error=False)
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Pragma": "no-cache"}


def _private_http_exception(
    status_code: int,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    merged = dict(_PRIVATE_HEADERS)
    merged.update(headers or {})
    return HTTPException(status_code, detail, headers=merged)


def _require_history_access(
    credentials: HTTPAuthorizationCredentials | None = Depends(_history_bearer),
) -> None:
    """Protege dados pessoais persistidos com Bearer configurado pelo operador."""
    configured = cfg.academia_history_token
    if not cfg.history_configured or configured is None:
        raise _private_http_exception(404, "histórico não habilitado neste ambiente.")
    supplied = credentials.credentials if credentials else ""
    if not _history_token_matches(supplied):
        raise _private_http_exception(
            401,
            "credencial inválida para o histórico protegido.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _history_token_matches(supplied: str) -> bool:
    configured = cfg.academia_history_token
    if not cfg.history_configured or configured is None or not supplied:
        return False
    return secrets.compare_digest(supplied, configured.get_secret_value())


def _require_persistence_access(request: Request) -> None:
    raw = request.headers.get("authorization", "")
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not _history_token_matches(token.strip()):
        raise _private_http_exception(
            401,
            "credencial administrativa obrigatória para persistir a análise.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


@router.get("/", include_in_schema=False)
def frontend():
    if not _FRONTEND.exists():
        raise HTTPException(404, "frontend não encontrado")
    return FileResponse(_FRONTEND, media_type="text/html")


@router.get("/health", response_model=AcademiaHealthResponse)
def health():
    """Capacidade do pipeline, sem expor credenciais."""
    profiles = list_profiles()
    primary = profiles[0]
    return {
        "configured": cfg.configured,
        "analysis_model": cfg.analysis_model,
        "tts_model": cfg.tts_model,
        "tts_voice": cfg.tts_voice,
        "max_upload_mb": cfg.max_upload_mb,
        "max_duration_seconds": cfg.academia_video_max_seconds,
        "analysis_fps": cfg.academia_fps,
        "identification_fps": cfg.academia_identification_fps,
        "identification_mode": "automatic",
        "automatic_identification": {
            "exercise": True,
            "variation": True,
            "equipment": True,
            "multiple_people_targeting": True,
            "active_interval": True,
        },
        "voice_transcription": {
            "available": cfg.configured and audio_tools_available(),
            "model": cfg.transcription_model,
            "max_duration_seconds": cfg.academia_voice_max_seconds,
            "max_upload_mb": cfg.academia_voice_max_upload_mb,
        },
        "persistence_available": cfg.persistence_available,
        "recommended_repetitions": {
            "min": primary.recommended_min_reps,
            "max": primary.recommended_max_reps,
        },
        "profiles": [
            {
                "exercise": profile.slug,
                "label": profile.label,
                "methodology_version": profile.methodology_version,
                "methodology_status": profile.methodology_status,
                "methodology_notice": profile.methodology_notice,
                "capture_guidance": list(profile.capture_guidance),
            }
            for profile in profiles
        ],
    }


@router.post(
    "/transcribe-target",
    response_model=TargetDescriptionTranscriptionResponse,
)
async def transcribe_target(
    request: Request,
    response: Response,
    audio: UploadFile = File(
        ...,
        description=(
            "Gravação curta da descrição visual/posição da pessoa-alvo. "
            "É normalizada para WAV, transcrita pelo Gemini e removida."
        ),
    ),
    consent: bool = Form(
        False,
        description="Consentimento para processar temporariamente a gravação de voz.",
    ),
):
    _private_no_store(response)
    if not consent:
        raise _private_http_exception(
            422,
            "confirme o consentimento para transcrever temporariamente a gravação.",
        )
    if not cfg.configured:
        raise _private_http_exception(
            503,
            "GEMINI_API_KEY não configurada — transcrição indisponível.",
        )
    _enforce_voice_content_length(request)
    try:
        return await service.transcribe_target_upload(audio)
    except EmptyVoiceAudio:
        raise _private_http_exception(400, "grave uma descrição antes de enviar.")
    except VoiceAudioTooLarge as exc:
        raise _private_http_exception(413, str(exc))
    except VoiceAudioTooLong as exc:
        raise _private_http_exception(413, str(exc))
    except InvalidVoiceAudio as exc:
        raise _private_http_exception(415, str(exc))
    except NoSpeechDetected as exc:
        raise _private_http_exception(422, str(exc))
    except VoiceTranscriptionBusy as exc:
        raise _private_http_exception(
            429,
            str(exc),
            headers={"Retry-After": "5"},
        )
    except VoiceTranscriptionUnavailable as exc:
        raise _private_http_exception(503, str(exc))
    except GeminiError:
        raise _private_http_exception(
            502,
            "falha temporária na transcrição; tente novamente ou digite a descrição.",
        )


@router.post("/analyze", response_model=AcademiaAnalysisResponse)
async def analyze(
    request: Request,
    response: Response,
    file: UploadFile = File(
        ...,
        description=(
            "Vídeo de uma série de musculação. Exercício, variação e equipamento "
            "são identificados automaticamente."
        ),
    ),
    practitioner_name: str | None = Form(None, description="Nome/rótulo opcional."),
    practitioner_id: str | None = Form(
        None, description="Identificador opaco opcional para evolução futura."
    ),
    practitioner_outfit: str | None = Form(
        None, description="Roupa/aparência para seguir a pessoa certa."
    ),
    practitioner_notes: str | None = Form(None, description="Contexto visual opcional."),
    capture_angle: str | None = Form(
        "unknown", description="frontal | lateral | posterior | diagonal | unknown"
    ),
    duration_seconds: float | None = Form(
        None,
        description=(
            "Metadado opcional do navegador; o teto usa somente duração aferida no servidor."
        ),
    ),
    with_audio: bool = Form(True, description="Gerar a versão falada do relatório."),
    persist: bool | None = Form(
        None,
        description=(
            "Opt-in explícito; exige persistência e histórico protegido habilitados no servidor."
        ),
    ),
    consent: bool = Form(
        False,
        description="Consentimento para processar temporariamente o vídeo corporal.",
    ),
):
    _private_no_store(response)
    if not consent:
        raise HTTPException(
            422,
            "confirme o consentimento para o processamento temporário do vídeo.",
        )
    if not cfg.configured:
        raise HTTPException(503, "GEMINI_API_KEY não configurada — análise indisponível.")
    if persist is True and cfg.persistence_available:
        _require_persistence_access(request)
    _enforce_content_length(request)
    try:
        practitioner = PractitionerHint(
            id=_blank_to_none(practitioner_id),
            name=_blank_to_none(practitioner_name),
            outfit=_blank_to_none(practitioner_outfit),
            notes=_blank_to_none(practitioner_notes),
        )
        return await service.analyze_upload(
            file,
            practitioner=practitioner,
            capture_angle=capture_angle,
            duration_hint=duration_seconds,
            with_audio=with_audio,
            persist=persist,
        )
    except UploadTooLarge as exc:
        raise HTTPException(413, str(exc))
    except VideoTooLong as exc:
        raise HTTPException(413, str(exc))
    except UnverifiableVideoDuration as exc:
        raise HTTPException(422, str(exc))
    except EmptyUpload:
        raise HTTPException(400, "envie um arquivo de vídeo.")
    except InvalidVideo as exc:
        raise HTTPException(415, str(exc))
    except AnalysisBusy as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "5"})
    except (ValueError, ValidationError) as exc:
        emit(
            catalog.ACADEMIA_ANALYZE_FAILED,
            level="warning",
            status="error",
            message="parâmetros inválidos na análise de academia",
            data={"stage": "validation"},
        )
        raise HTTPException(422, str(exc))
    except GeminiError as exc:
        emit(
            catalog.ACADEMIA_ANALYZE_FAILED,
            level="error",
            status="error",
            error=exc,
            data={"stage": "gemini"},
        )
        raise HTTPException(
            502,
            "falha temporária no serviço de análise; tente novamente em alguns instantes.",
        )


@router.get(
    "/analyses",
    response_model=AcademiaHistoryResponse,
    response_model_exclude_none=True,
)
def list_analyses(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _authorized: None = Depends(_require_history_access),
):
    _private_no_store(response)
    try:
        items = store.list_analyses(limit=limit, offset=offset)
    except Exception as exc:
        emit(
            catalog.ACADEMIA_WARNING,
            level="warning",
            error=exc,
            data={"stage": "history_list"},
        )
        return {
            "items": [],
            "limit": limit,
            "offset": offset,
            "available": False,
            "warning": "histórico temporariamente indisponível.",
        }
    emit(
        catalog.ACADEMIA_ANALYSIS_RETRIEVED,
        data={"kind": "list", "count": len(items)},
    )
    available = store.available()
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "available": available,
        "warning": None if available else "histórico indisponível sem pool de banco.",
    }


@router.get("/analyses/{analysis_id}", response_model=AcademiaStoredAnalysis)
def get_analysis(
    analysis_id: int,
    response: Response,
    _authorized: None = Depends(_require_history_access),
):
    _private_no_store(response)
    try:
        record = store.get_analysis(analysis_id)
    except Exception as exc:
        emit(catalog.ACADEMIA_WARNING, level="warning", error=exc,
             data={"stage": "history_detail"})
        raise _private_http_exception(503, "histórico temporariamente indisponível.")
    if record is None:
        raise _private_http_exception(
            404, "análise não encontrada (ou persistência indisponível)."
        )
    emit(
        catalog.ACADEMIA_ANALYSIS_RETRIEVED,
        data={"kind": "detail", "id": analysis_id},
    )
    return record


@router.get("/analyses/{analysis_id}/audio")
def get_analysis_audio(
    analysis_id: int,
    _authorized: None = Depends(_require_history_access),
):
    try:
        wav = store.get_audio(analysis_id)
    except Exception as exc:
        emit(catalog.ACADEMIA_WARNING, level="warning", error=exc,
             data={"stage": "history_audio"})
        raise _private_http_exception(503, "histórico temporariamente indisponível.")
    if wav is None:
        raise _private_http_exception(404, "áudio não encontrado para esta análise.")
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={
            "Content-Disposition": (
                f'inline; filename="bitvar-academia-analise-{analysis_id}.wav"'
            ),
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
        },
    )


@router.get("/analyses/{analysis_id}/export")
def export_analysis(
    analysis_id: int,
    format: str = Query("txt", pattern="^(txt|json)$"),
    _authorized: None = Depends(_require_history_access),
):
    try:
        record = store.get_analysis(analysis_id)
    except Exception as exc:
        emit(catalog.ACADEMIA_WARNING, level="warning", error=exc,
             data={"stage": "history_export"})
        raise _private_http_exception(503, "histórico temporariamente indisponível.")
    if record is None:
        raise _private_http_exception(
            404, "análise não encontrada (ou persistência indisponível)."
        )
    emit(
        catalog.ACADEMIA_ANALYSIS_EXPORTED,
        data={"id": analysis_id, "format": format},
    )
    base = f"bitvar-academia-analise-{analysis_id}"
    if format == "json":
        body = json.dumps(record, ensure_ascii=False, indent=2, default=str)
        return Response(
            content=body,
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{base}.json"',
                "Cache-Control": "private, no-store",
                "Pragma": "no-cache",
            },
        )
    return Response(
        content=_render_txt_report(record),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{base}.txt"',
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
        },
    )


@router.delete("/analyses/{analysis_id}", response_model=AcademiaDeleteResponse)
def delete_analysis(
    analysis_id: int,
    response: Response,
    _authorized: None = Depends(_require_history_access),
):
    _private_no_store(response)
    try:
        deleted = store.delete_analysis(analysis_id)
    except Exception as exc:
        emit(
            catalog.ACADEMIA_WARNING,
            level="warning",
            error=exc,
            data={"stage": "history_delete"},
        )
        raise _private_http_exception(503, "histórico temporariamente indisponível.")
    if not deleted:
        raise _private_http_exception(
            404, "análise não encontrada (ou persistência indisponível)."
        )
    emit(catalog.ACADEMIA_ANALYSIS_DELETED, data={"id": analysis_id})
    return {"deleted": True, "id": analysis_id}


def _render_txt_report(record: dict) -> str:
    """Relatório compartilhável: acertos antes das correções e limites explícitos."""
    result = record.get("result_json") or {}
    route = result.get("route") or {}
    identification = result.get("identification") or {}
    metrics = result.get("metrics") or {}
    capture = metrics.get("capture_quality") or {}
    movement = metrics.get("movement_summary") or metrics.get("movement") or {}
    score = metrics.get("weighted_execution_score") or {}
    status = record.get("analysis_status") or result.get("analysis_status") or "unknown"
    raw_scope = (
        identification.get("methodology_scope")
        or route.get("methodology_scope")
        or metrics.get("methodology_scope")
    )
    is_general = (
        raw_scope in {"general_execution", "generic_execution"}
        or metrics.get("analysis_mode") == "general_execution"
    )
    if is_general:
        scope_label = "Análise observacional genérica"
    elif raw_scope == "none" or not metrics:
        scope_label = "Somente identificação"
    else:
        scope_label = "Metodologia específica do exercício"
    exercise_label = (
        identification.get("exercise_label")
        or metrics.get("exercise_label")
        or route.get("exercise_label")
        or record.get("exercise")
        or "Exercício não informado"
    )
    methodology_version = (
        record.get("methodology_version")
        or metrics.get("methodology_version")
        or route.get("methodology_version")
        or "não informada"
    )
    lines = [
        f"BITVAR IA — Análise de Academia #{record.get('id')}",
        f"Exercício: {exercise_label}",
        f"Data: {record.get('created_at')}",
        f"Status: {status}",
        f"Tipo de análise: {scope_label}",
        (
            "Metodologia: "
            f"{methodology_version} "
            "(POC provisória, ainda não validada por especialista)"
        ),
        "",
        "CAPTURA E SEGMENTAÇÃO",
        f"Qualidade: {capture.get('status', 'não informada')}",
        (
            "Repetições observadas para análise: "
            f"{movement.get('complete_repetitions', 0)} completas de "
            f"{movement.get('detected_repetitions', 0)} detectadas"
        ),
    ]
    if not is_general and score.get("score") is not None:
        lines.append(
            f"Indicador visual desta POC: {score['score']}/100 "
            f"(cobertura {round(float(score.get('coverage', 0)) * 100)}%)"
        )
    if is_general:
        summary = metrics.get("execution_summary") or {}
        reliability = summary.get("reliability") or {}
        classification_labels = {
            "adequada_ao_padrao_observado": "Adequada ao padrão observado",
            "parcialmente_adequada": "Parcialmente adequada",
            "necessita_ajustes": "Necessita de ajustes",
            "nao_avaliavel": "Não avaliável",
        }
        confidence_labels = {"baixa": "Baixa", "media": "Média", "alta": "Alta"}
        lines += [
            "",
            "CLASSIFICAÇÃO DA EXECUÇÃO",
            classification_labels.get(
                summary.get("classification"),
                str(summary.get("classification") or "Não informada"),
            ),
            "",
            "CONFIABILIDADE E COBERTURA",
        ]
        if reliability.get("level"):
            lines.append(
                f"- Nível: {confidence_labels.get(reliability['level'], reliability['level'])}"
            )
        coverage = reliability.get("coverage")
        if isinstance(coverage, (int, float)):
            percentage = coverage * 100 if coverage <= 1 else coverage
            lines.append(f"- Cobertura observacional: {round(percentage)}%")
        if (
            reliability.get("evaluated_criteria") is not None
            and reliability.get("applicable_criteria") is not None
        ):
            lines.append(
                "- Critérios avaliados: "
                f"{reliability['evaluated_criteria']}/"
                f"{reliability['applicable_criteria']} aplicáveis"
            )
        if reliability.get("complete_repetitions") is not None:
            lines.append(
                f"- Repetições completas: {reliability['complete_repetitions']}"
            )
        lines += [f"- {item}" for item in reliability.get("basis") or []]
        lines.append(
            "A cobertura informa observabilidade; não é probabilidade de acerto nem nota."
        )

        relevance = metrics.get("training_relevance") or {}
        value_labels = {
            "lento_controlado": "Lento e controlado",
            "moderado_controlado": "Moderado e controlado",
            "rapido_controlado": "Rápido e controlado",
            "rapido_sem_controle": "Rápido com perda de controle observável",
            "irregular": "Irregular",
            "nao_avaliavel": "Não avaliável",
        }
        lines += ["", "RELEVÂNCIA CONDICIONAL PARA O TREINO"]
        if relevance.get("observed_style"):
            lines.append(
                "- Ritmo observado: "
                + value_labels.get(
                    relevance["observed_style"], relevance["observed_style"]
                )
            )
        lines += [
            f"- Ênfase observável: {item}"
            for item in relevance.get("observable_emphasis") or []
        ]
        if relevance.get("performance_interpretation"):
            lines.append(
                "- Interpretação condicional: "
                f"{relevance['performance_interpretation']}"
            )
        lines += [
            f"- Não determinável sem: {item}"
            for item in relevance.get("cannot_determine_without") or []
        ]
        lines.append(
            "Um vídeo isolado não comprova eficácia, hipertrofia, ganho de força "
            "ou desempenho futuro."
        )

        movement_labels = {
            **value_labels,
            "consistente": "Consistente",
            "variavel": "Variável",
            "inconclusivo": "Inconclusivo",
        }
        lines += ["", "LEITURA DO MOVIMENTO"]
        for label, key in (
            ("Ritmo", "tempo_style"),
            ("Consistência do ritmo", "tempo_consistency"),
            ("Consistência da amplitude", "range_consistency"),
            ("Consistência da trajetória", "trajectory_consistency"),
            ("Observação geral", "overall_observation"),
        ):
            value = movement.get(key)
            if value not in (None, ""):
                lines.append(f"- {label}: {movement_labels.get(value, value)}")
        if movement.get("average_repetition_seconds") is not None:
            lines.append(
                "- Duração média por repetição: "
                f"{movement['average_repetition_seconds']}s"
            )
        if movement.get("repetition_duration_variation") is not None:
            variation = float(movement["repetition_duration_variation"])
            percentage = variation * 100 if variation <= 1 else variation
            lines.append(
                f"- Variação relativa da duração: {round(percentage)}%"
            )

        repetitions = metrics.get("repetitions") or []
        if repetitions:
            lines += ["", "REPETIÇÕES E SEGMENTOS"]
            for item in repetitions:
                if not isinstance(item, dict):
                    continue
                number = item.get("index", "?")
                complete = "completa" if item.get("complete") else "incompleta"
                markers = []
                for label, key in (
                    ("início", "start_s"),
                    ("transição", "transition_s"),
                    ("fim", "end_s"),
                ):
                    if item.get(key) is not None:
                        markers.append(f"{label} {item[key]}s")
                suffix = f" ({' · '.join(markers)})" if markers else ""
                lines.append(f"- Repetição {number}: {complete}{suffix}")

    lines += ["", "PONTOS CORRETOS"]
    positives = metrics.get("positive_points") or []
    lines += [f"- {item}" for item in positives] or ["- Nenhum ponto pôde ser confirmado com segurança."]

    lines += ["", "ANÁLISE TÉCNICA DA EXECUÇÃO — CHECKLIST OBSERVACIONAL"]
    verdict_labels = {
        "adequado": "Adequado",
        "ajuste_leve": "Refinamento sugerido",
        "a_corrigir": "A corrigir",
        "nao_avaliavel": "Não avaliável nesta captura",
        "nao_aplicavel": "Não aplicável a este exercício",
    }
    for item in metrics.get("checklist") or []:
        label = item.get("label") or item.get("id")
        verdict = verdict_labels.get(item.get("verdict"), item.get("verdict"))
        lines.append(f"- {label}: {verdict}. {item.get('observation', '')}".rstrip())
        suggestion = (
            item.get("coaching_suggestion")
            or item.get("correction")
            or item.get("recommendation")
        )
        if suggestion:
            lines.append(f"  Sugestão prática: {suggestion}")
        if item.get("muscle_context"):
            lines.append(f"  Contexto muscular: {item['muscle_context']}")
    if is_general:
        lines.append(
            "Checklist observacional geral; não substitui uma metodologia específica "
            "do exercício."
        )

    lines += ["", "FOCO PRÁTICO DE EVOLUÇÃO"]
    lines.append(
        metrics.get("priority_improvement")
        or "Nenhum foco adicional foi indicado nesta análise."
    )
    secondary = metrics.get("secondary_improvements") or []
    if secondary:
        lines += ["", "OUTRAS SUGESTÕES PRÁTICAS"]
        lines += [f"- {item}" for item in secondary]

    limitations = metrics.get("limitations") or capture.get("issues") or []
    if limitations:
        lines += ["", "LIMITAÇÕES DA LEITURA"]
        lines += [f"- {item}" for item in limitations]
    narrative = result.get("narrative")
    if narrative:
        lines += ["", "RELATÓRIO ACESSÍVEL", narrative]
    muscle_notice = metrics.get("muscle_activation_notice")
    if muscle_notice:
        lines += ["", "CONTEXTO MUSCULAR", muscle_notice]
    expected_roles = metrics.get("expected_muscle_roles") or []
    if expected_roles:
        lines += ["", "PAPÉIS MUSCULARES ESPERADOS"]
        lines += [f"- {item}" for item in expected_roles]
    references = metrics.get("literature_references") or []
    if references:
        lines += ["", "REFERÊNCIAS DA METODOLOGIA"]
        for reference in references:
            if isinstance(reference, dict):
                citation = reference.get("citation") or "Referência"
                url = reference.get("url")
                lines.append(f"- {citation}" + (f" — {url}" if url else ""))
    lines += [
        "",
        (
            "Aviso: análise visual educacional de POC. Não é diagnóstico, não mede "
            "risco de lesão e não substitui personal, fisioterapeuta ou profissional de saúde."
        ),
        "— gerado por BITVAR IA",
    ]
    return "\n".join(lines)


def _enforce_content_length(request: Request) -> None:
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > cfg.max_upload_bytes:
        raise HTTPException(413, f"arquivo acima do limite de {cfg.max_upload_mb} MB")


def _enforce_voice_content_length(request: Request) -> None:
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > cfg.voice_max_request_body_bytes:
        raise _private_http_exception(
            413,
            f"gravação acima do limite de {cfg.academia_voice_max_upload_mb} MB.",
        )


def _blank_to_none(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


__all__ = ["router", "service"]
