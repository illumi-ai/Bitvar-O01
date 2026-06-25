"""Endpoints HTTP do módulo de tênis (blueprint §01, §07, §10).

* ``GET  /tennis/``        → frontend (dashboard das três saídas)
* ``GET  /tennis/health``  → status do pipeline (sem expor a chave)
* ``POST /tennis/analyze`` → upload do vídeo → métricas + texto + áudio

Guarda de tamanho: o ``Content-Length`` é checado **antes** de ler o corpo
(413), e o serviço revalida o tamanho exato ao gravar em disco.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

from app.events import catalog, emit

from . import store
from .config import tennis_settings as cfg
from .gemini import GeminiError
from .models import SubjectHint, TennisAnalysisResponse
from .service import ClipTooLong, EmptyUpload, TennisService, UploadTooLarge

router = APIRouter(prefix="/tennis", tags=["tennis"])
service = TennisService(cfg)

_FRONTEND = Path(__file__).resolve().parent.parent / "static" / "tennis" / "index.html"
_VALID_HANDEDNESS = ("destro", "canhoto", "indeterminado")


@router.get("/", include_in_schema=False)
def frontend():
    if not _FRONTEND.exists():
        raise HTTPException(404, "frontend não encontrado")
    return FileResponse(_FRONTEND, media_type="text/html")


@router.get("/health")
def health():
    """Pronto para analisar? Mostra modelos e limites, nunca a chave."""
    return {
        "configured": cfg.configured,
        "analysis_model": cfg.analysis_model,
        "tts_model": cfg.tts_model,
        "tts_voice": cfg.tts_voice,
        "max_upload_mb": cfg.max_upload_mb,
        "clip_max_seconds": cfg.clip_max_seconds,
    }


@router.post("/analyze", response_model=TennisAnalysisResponse)
async def analyze(
    request: Request,
    file: UploadFile = File(..., description="Vídeo do lance (clip) ou da partida (match)."),
    gender: str = Form("male", description="male | female (aceita m/f, masculino/feminino)."),
    level: str = Form("amador", description="amador | profissional (aceita pro/amateur; default amador)."),
    mode: str | None = Form(None, description="Override: clip | match | auto (default: auto)."),
    duration_seconds: float | None = Form(None, description="Duração conhecida (opcional)."),
    with_audio: bool = Form(True, description="Gerar áudio TTS (saída 3)."),
    persist: bool | None = Form(None, description="Forçar/!forçar persistência (default: config)."),
    player_name: str | None = Form(None, description="Nome do jogador a analisar."),
    player_outfit: str | None = Form(None, description="Roupa/aparência (ex.: camiseta azul, boné)."),
    player_side: str | None = Form(None, description="Lado/posição na quadra (ex.: fundo esquerdo)."),
    player_notes: str | None = Form(None, description="Outras dicas para identificar o jogador."),
    player_handedness: str | None = Form(None, description="Lateralidade: destro | canhoto."),
    player_headwear: str | None = Form(None, description="Boné/viseira e cor (ex.: boné branco)."),
    player_racket_color: str | None = Form(None, description="Cor/marca da raquete."),
    player_glasses: bool | None = Form(None, description="Usa óculos?"),
    player_hair: str | None = Form(None, description="Cabelo (cor/comprimento)."),
    camera_position: str | None = Form(None, description="Posição da câmera (1 por lado): fundo_meu | fundo_adv | lateral_esq | lateral_dir (aceita também fundo/lateral/central legados)."),
    target_quadrant: str | None = Form(None, description="Quadrante onde o atleta-alvo começa o ponto (1-4)."),
    target_appearance: str | None = Form(None, description="Cor/aparência do alvo (fio de continuidade; ex.: camisa e short azul)."),
):
    if not cfg.configured:
        raise HTTPException(503, "GEMINI_API_KEY não configurada — análise indisponível.")
    _enforce_content_length(request)
    hand = player_handedness if player_handedness in _VALID_HANDEDNESS else None
    subject = SubjectHint(
        name=player_name, outfit=player_outfit, side=player_side, notes=player_notes,
        handedness=hand, headwear=player_headwear, racket_color=player_racket_color,
        glasses=player_glasses, hair=player_hair,
    )
    try:
        return await service.analyze_upload(
            file,
            gender=gender,
            level=level,
            mode_override=mode,
            duration_hint=duration_seconds,
            with_audio=with_audio,
            persist=persist,
            subject=subject,
            camera_position=camera_position,
            target_quadrant=target_quadrant,
            target_appearance=target_appearance,
        )
    except UploadTooLarge as e:
        raise HTTPException(413, str(e))
    except ClipTooLong as e:
        raise HTTPException(413, str(e))
    except EmptyUpload:
        raise HTTPException(400, "envie um arquivo de vídeo.")
    except ValueError as e:  # gênero/override inválido
        emit(catalog.TENNIS_ANALYZE_FAILED, level="warning", status="error", error=e,
             data={"stage": "validation"})
        raise HTTPException(422, str(e))
    except GeminiError as e:
        emit(catalog.TENNIS_ANALYZE_FAILED, level="error", status="error", error=e,
             data={"stage": "gemini"})
        raise HTTPException(502, f"falha no Gemini: {e}")


# --------------------------------------------------------------------------- #
# histórico & exportação (spec E1 — "como salvo e te mando de volta?")          #
# --------------------------------------------------------------------------- #
@router.get("/analyses")
def list_analyses(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Histórico paginado das análises salvas (vazio se persistência off/DB fora)."""
    items = store.list_analyses(limit=limit, offset=offset)
    emit(catalog.TENNIS_ANALYSIS_RETRIEVED, data={"count": len(items), "kind": "list"})
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int):
    """Análise completa por id (métricas + narrativa); 404 se não existir."""
    rec = store.get_analysis(analysis_id)
    if rec is None:
        raise HTTPException(404, "análise não encontrada (ou persistência indisponível).")
    emit(catalog.TENNIS_ANALYSIS_RETRIEVED, data={"id": analysis_id, "kind": "detail"})
    return rec


@router.get("/analyses/{analysis_id}/audio")
def get_analysis_audio(analysis_id: int):
    """Áudio (WAV) da narrativa de uma análise salva; 404 se não houver."""
    wav = store.get_audio(analysis_id)
    if wav is None:
        raise HTTPException(404, "áudio não encontrado para esta análise.")
    return Response(
        content=wav, media_type="audio/wav",
        headers={"Content-Disposition": f'inline; filename="bitvar-analise-{analysis_id}.wav"'},
    )


@router.get("/analyses/{analysis_id}/export")
def export_analysis(
    analysis_id: int,
    format: str = Query("txt", pattern="^(txt|json)$"),
):
    """Exporta o relatório para baixar/compartilhar — texto legível ou JSON cru."""
    rec = store.get_analysis(analysis_id)
    if rec is None:
        raise HTTPException(404, "análise não encontrada (ou persistência indisponível).")
    emit(catalog.TENNIS_ANALYSIS_EXPORTED, data={"id": analysis_id, "format": format})
    base = f"bitvar-analise-{analysis_id}"
    if format == "json":
        body = json.dumps(rec, ensure_ascii=False, indent=2, default=str)
        return Response(
            content=body, media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base}.json"'},
        )
    text = _render_txt_report(rec)
    return Response(
        content=text, media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{base}.txt"'},
    )


def _render_txt_report(rec: dict) -> str:
    """Relatório legível (PT-BR) a partir do registro salvo — pronto p/ WhatsApp."""
    result = rec.get("result_json") or {}
    m = result.get("metrics") or {}
    narrative = result.get("narrative") or ""
    lines = [
        f"BITVAR IA — Análise de Tênis #{rec.get('id')}",
        f"Data: {rec.get('created_at')}",
        f"Gênero: {rec.get('gender')} · Modo: {rec.get('mode')}",
        "",
    ]
    if rec.get("mode") == "clip":
        bits = []
        if m.get("shot_identified"):
            bits.append(f"Golpe: {m['shot_identified']}")
        if m.get("action_phase"):
            bits.append(f"Fase: {m['action_phase']}")
        if m.get("phase_alternative"):
            bits.append(f"Fase alternativa: {m['phase_alternative']}")
        if m.get("clip_quality_score") is not None:
            bits.append(f"Nota técnica: {m['clip_quality_score']}/10")
        ws = m.get("weighted_performance_score") or {}
        if ws.get("score") is not None:
            bits.append(f"Nota oficial: {ws['score']}/100 ({ws.get('weighting_model', '')})")
        if bits:
            lines += [" · ".join(bits), ""]
    else:
        ws = m.get("weighted_performance_score") or {}
        if ws.get("score") is not None:
            lines += [f"Score ponderado: {ws['score']}/100 ({ws.get('weighting_model', '')})", ""]
    if m.get("key_improvement"):
        lines += ["PRINCIPAL CORREÇÃO", m["key_improvement"], ""]
    secondary = m.get("secondary_improvements") or []
    if secondary:
        lines.append("CORREÇÕES SECUNDÁRIAS")
        lines += [f"- {s}" for s in secondary]
        lines.append("")
    if narrative:
        lines += ["RELATÓRIO DO TREINADOR", narrative, ""]
    lines.append("— gerado por BITVAR IA")
    return "\n".join(lines)


def _enforce_content_length(request: Request) -> None:
    """Rejeita cedo se o Content-Length já estoura o limite (antes de ler o corpo)."""
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > cfg.max_upload_bytes:
        raise HTTPException(413, f"arquivo acima do limite de {cfg.max_upload_mb} MB")
