"""Endpoints HTTP do módulo de academia (espelha ``app/tennis/router.py``).

* ``GET  /academia/``        → frontend (dashboard das três saídas)
* ``GET  /academia/health``  → status do pipeline (sem expor a chave)
* ``POST /academia/analyze`` → upload do vídeo → métricas + narrativa + áudio

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
from .config import academia_settings as cfg
from .gemini import GeminiError
from .models import AcademiaAnalysisResponse
from .service import AcademiaService, ClipTooLong, EmptyUpload, UploadTooLarge

router = APIRouter(prefix="/academia", tags=["academia"])
service = AcademiaService(cfg)

_FRONTEND = Path(__file__).resolve().parent.parent / "static" / "academia" / "index.html"


@router.get("/", include_in_schema=False)
def frontend():
    if not _FRONTEND.exists():
        raise HTTPException(404, "frontend não encontrado")
    # no-cache: o HTML muda a cada deploy e é servido sem hash no nome; sem isto o
    # navegador cacheia por heurística e mostra a versão antiga depois de publicar
    # (o "não vejo as alterações"). "no-cache" mantém o cache, mas força revalidação
    # via ETag a cada visita → 304 barato se nada mudou, 200 com o novo HTML após deploy.
    return FileResponse(
        _FRONTEND, media_type="text/html", headers={"Cache-Control": "no-cache"}
    )


@router.get("/health")
def health():
    """Pronto para analisar? Mostra modelos e limites, nunca a chave."""
    return {
        "configured": cfg.configured,
        "analysis_model": cfg.academia_analysis_model,
        "tts_model": cfg.academia_tts_model,
        "tts_voice": cfg.academia_tts_voice,
        "max_upload_mb": cfg.academia_max_upload_mb,
        "clip_max_seconds": cfg.academia_clip_max_seconds,
    }


@router.post("/analyze", response_model=AcademiaAnalysisResponse)
async def analyze(
    request: Request,
    file: UploadFile = File(..., description="Vídeo do exercício de academia."),
    student_name: str | None = Form(None, description="Nome do aluno (personaliza a narrativa, RF-007)."),
    duration_seconds: float | None = Form(None, description="Duração conhecida (opcional)."),
    with_audio: bool = Form(True, description="Gerar áudio TTS (saída 3)."),
    persist: bool | None = Form(None, description="Forçar/!forçar persistência (default: config, opt-in)."),
):
    if not cfg.configured:
        raise HTTPException(503, "GEMINI_API_KEY não configurada — análise indisponível.")
    _enforce_content_length(request)
    try:
        return await service.analyze_upload(
            file,
            student_name=student_name,
            duration_hint=duration_seconds,
            with_audio=with_audio,
            persist=persist,
        )
    except UploadTooLarge as e:
        raise HTTPException(413, str(e))
    except ClipTooLong as e:
        raise HTTPException(413, str(e))
    except EmptyUpload:
        raise HTTPException(400, "envie um arquivo de vídeo.")
    except ValueError as e:
        emit(catalog.ACADEMIA_ANALYZE_FAILED, level="warning", status="error", error=e,
             data={"stage": "validation"})
        raise HTTPException(422, str(e))
    except GeminiError as e:
        emit(catalog.ACADEMIA_ANALYZE_FAILED, level="error", status="error", error=e,
             data={"stage": "gemini"})
        raise HTTPException(502, f"falha no Gemini: {e}")


# --------------------------------------------------------------------------- #
# histórico & exportação (espelha o padrão E1 do tênis)                        #
# --------------------------------------------------------------------------- #
@router.get("/analyses")
def list_analyses(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Histórico paginado das análises salvas (vazio se persistência off/DB fora)."""
    items = store.list_analyses(limit=limit, offset=offset)
    emit(catalog.ACADEMIA_ANALYSIS_RETRIEVED, data={"count": len(items), "kind": "list"})
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int):
    """Análise completa por id (métricas + narrativa); 404 se não existir."""
    rec = store.get_analysis(analysis_id)
    if rec is None:
        raise HTTPException(404, "análise não encontrada (ou persistência indisponível).")
    emit(catalog.ACADEMIA_ANALYSIS_RETRIEVED, data={"id": analysis_id, "kind": "detail"})
    return rec


@router.get("/analyses/{analysis_id}/audio")
def get_analysis_audio(analysis_id: int):
    """Áudio (WAV) da narrativa de uma análise salva; 404 se não houver."""
    wav = store.get_audio(analysis_id)
    if wav is None:
        raise HTTPException(404, "áudio não encontrado para esta análise.")
    return Response(
        content=wav, media_type="audio/wav",
        headers={"Content-Disposition": f'inline; filename="bitvar-academia-{analysis_id}.wav"'},
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
    emit(catalog.ACADEMIA_ANALYSIS_EXPORTED, data={"id": analysis_id, "format": format})
    base = f"bitvar-academia-{analysis_id}"
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
        f"BITVAR IA — Análise de Academia #{rec.get('id')}",
        f"Data: {rec.get('created_at')}",
        f"Aluno: {rec.get('student_name') or '-'}",
        "",
    ]
    bits = []
    if m.get("exercicio_identificado"):
        bits.append(f"Exercício: {m['exercicio_identificado']}")
    if m.get("veredito"):
        bits.append(f"Veredito: {m['veredito']}")
    if m.get("risco_lesao"):
        bits.append("RISCO DE LESÃO")
    if bits:
        lines += [" · ".join(bits), ""]
    if m.get("foco_pratico"):
        lines += ["FOCO PRÁTICO PRINCIPAL", m["foco_pratico"], ""]
    erros = m.get("erros") or []
    if erros:
        lines.append("O QUE ESTÁ ERRADO → COMO CONSERTAR")
        for e in erros:
            lines.append(f"- [{e.get('gravidade')}] {e.get('descricao')}")
            if e.get("correcao"):
                lines.append(f"    corrigir: {e['correcao']}")
        lines.append("")
    acertos = m.get("acertos") or []
    if acertos:
        lines.append("ACERTOS")
        lines += [f"- {a}" for a in acertos]
        lines.append("")
    if narrative:
        lines += ["RELATÓRIO DO PERSONAL TRAINER", narrative, ""]
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
        raise HTTPException(413, f"arquivo acima do limite de {cfg.academia_max_upload_mb} MB")
