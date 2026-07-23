"""Orquestrador do pipeline de academia (espelha ``app/tennis/service.py``).

Salva o upload em disco em chunks (validando o tamanho), chama o Gemini
(vídeo→JSON→texto→áudio), persiste opcionalmente e devolve as três saídas. As
chamadas de rede (bloqueantes) rodam em threadpool para não travar o event
loop — mesmo padrão do tênis, incluindo a re-fixação do contexto de
correlação dentro do worker thread.

Diferente do tênis, não há roteamento por gênero/modo/duração-limiar: todo
vídeo de academia segue o mesmo ``fps``/``media_resolution`` fixos de
``config.py`` (um único schema, ``AcademiaAnalysis``). O teto de duração
(``academia_clip_max_seconds``) ainda é aplicado.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
import time

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool

from app.events import catalog, emit, get_correlation_id, set_context
from app.tennis.routing import probe_duration_seconds

from .config import AcademiaSettings
from .config import academia_settings as default_cfg
from .frames import extract_error_frames
from .gemini import AcademiaGemini, GeminiError
from .models import AcademiaAnalysis, AcademiaAnalysisResponse
from .prompts import analysis_system_prompt
from .scoring import compute_nota_execucao, finalize_veredito, harmonize_analysis

log = logging.getLogger("bitvar.academia")


class UploadTooLarge(Exception):
    """Upload excede ``academia_max_upload_mb``."""

    def __init__(self, limit_mb: int):
        super().__init__(f"arquivo acima do limite de {limit_mb} MB")
        self.limit_mb = limit_mb


class EmptyUpload(Exception):
    """Upload vazio ou ausente."""


class ClipTooLong(Exception):
    """Vídeo acima da duração máxima (``academia_clip_max_seconds``)."""

    def __init__(self, limit_s: float, duration: float | None = None):
        super().__init__(f"vídeo acima do limite de {limit_s:.0f}s — envie um clipe mais curto.")
        self.limit_s = limit_s
        self.duration = duration


class AcademiaService:
    def __init__(self, settings: AcademiaSettings = default_cfg, gemini: AcademiaGemini | None = None):
        self.cfg = settings
        self.gemini = gemini or AcademiaGemini(settings)

    async def analyze_upload(
        self,
        upload: UploadFile,
        *,
        student_name: str | None = None,
        duration_hint: float | None = None,
        with_audio: bool = True,
        persist: bool | None = None,
    ) -> AcademiaAnalysisResponse:
        t0 = time.monotonic()
        cid = get_correlation_id()
        emit(catalog.ACADEMIA_ANALYZE_RECEIVED, data={
            "filename": upload.filename, "content_type": upload.content_type,
            "student_name": student_name, "with_audio": with_audio,
        })
        try:
            path, size = await self._save_upload(upload)
        except UploadTooLarge as e:
            emit(catalog.ACADEMIA_UPLOAD_REJECTED, level="warning", status="error", error=e,
                 data={"reason": "too_large", "limit_mb": self.cfg.academia_max_upload_mb})
            raise
        except EmptyUpload:
            emit(catalog.ACADEMIA_UPLOAD_REJECTED, level="warning", status="error",
                 data={"reason": "empty"})
            raise
        try:
            emit(catalog.ACADEMIA_UPLOAD_SAVED, data={"size_mb": round(size / 1024 / 1024, 2)})
            duration = duration_hint or probe_duration_seconds(path)
            # teto de duração: rejeita se a duração for conhecida e exceder o limite.
            # Duração indeterminável → segue (degradação graciosa, mesmo padrão do tênis).
            if duration is not None and duration > self.cfg.academia_clip_max_seconds:
                emit(catalog.ACADEMIA_UPLOAD_REJECTED, level="warning", status="error",
                     data={"reason": "too_long", "duration_s": round(duration, 1),
                           "limit_s": self.cfg.academia_clip_max_seconds})
                raise ClipTooLong(self.cfg.academia_clip_max_seconds, duration)
            route_info = {
                "fps": self.cfg.academia_fps,
                "media_resolution": self.cfg.academia_media_resolution,
                "analysis_thinking_level": self.cfg.academia_analysis_thinking_level,
                "duration_s": round(duration, 1) if duration is not None else None,
            }
            emit(catalog.ACADEMIA_ROUTE_DECIDED, data=route_info)
            system_prompt = analysis_system_prompt(student_name, fps=self.cfg.academia_fps)
            mime = upload.content_type or "video/mp4"
            # tudo abaixo é rede bloqueante → threadpool
            return await run_in_threadpool(
                self._run_pipeline, path, mime, system_prompt, with_audio, persist,
                cid, t0, student_name,
            )
        finally:
            _safe_remove(path)

    # ----- pipeline bloqueante (rede) -----
    def _run_pipeline(
        self, path: str, mime: str, system_prompt: str, with_audio: bool,
        persist: bool | None, correlation_id: str | None = None, t0: float | None = None,
        student_name: str | None = None,
    ) -> AcademiaAnalysisResponse:
        # re-fixa o contexto no worker thread (correlaciona os eventos do Gemini)
        set_context(correlation_id=correlation_id)
        warnings: list[str] = []

        file = self.gemini.upload_video(path, mime_type=mime)
        try:
            analysis: AcademiaAnalysis = self.gemini.analyze(
                file,
                schema_model=AcademiaAnalysis,
                system_prompt=system_prompt,
                fps=self.cfg.academia_fps,
                media_resolution=self.cfg.academia_media_resolution,
            )
        finally:
            self.gemini.delete_file(file)

        # harmonização determinística: consistência checklist↔erros e flag de
        # risco em CÓDIGO (cada ajuste vira um warning visível), depois a nota
        # 0..100 e por fim o veredito de 4 níveis DERIVADO da banda da nota —
        # aritmética 100% Python, o VLM só forneceu notas 0..10 e gravidades.
        analysis, ajustes = harmonize_analysis(analysis)
        warnings.extend(ajustes)
        nota = compute_nota_execucao(analysis)
        veredito_vlm = analysis.veredito
        warnings.extend(finalize_veredito(analysis, nota))
        emit(catalog.ACADEMIA_WEIGHTED_SCORE, data={
            "nota": nota.nota, "valida": nota.valida, "modelo_pesos": nota.modelo_pesos,
            "criterios_presentes": nota.criterios_presentes, "cobertura": nota.cobertura,
            "nota_pre_gates": nota.nota_pre_gates, "gate": nota.gate,
            "teto_aplicado": nota.teto_aplicado,
            "veredito": analysis.veredito, "veredito_vlm": veredito_vlm,
            "total_deducoes": round(sum(d.pontos for d in nota.deducoes), 2),
            "erros_leve": sum(1 for e in analysis.erros if e.gravidade == "leve"),
            "erros_moderada": sum(1 for e in analysis.erros if e.gravidade == "moderada"),
            "erros_risco": sum(1 for e in analysis.erros if e.gravidade == "risco_lesao"),
            "ajustes_consistencia": len(ajustes),
        })

        # prints do momento exato de cada erro (ffmpeg sobre o vídeo local, que
        # ainda existe aqui — é apagado pelo caller depois do threadpool). Só há
        # frames quando há erro com timestamp; falha vira warning, nunca erro.
        frames, frame_warnings = extract_error_frames(path, analysis.erros, self.cfg)
        warnings.extend(frame_warnings)
        if frames:
            emit(catalog.ACADEMIA_FRAMES_EXTRACTED, data={
                "count": len(frames),
                "timestamps_s": [f.timestamp_s for f in frames],
                "erros_com_timestamp": sum(1 for e in analysis.erros if e.timestamp_s is not None),
            })

        metrics = analysis.model_dump(by_alias=True, exclude_none=True)

        narrative: str | None = None
        audio_b64: str | None = None
        audio_wav: bytes | None = None
        try:
            # a narrativa recebe também a nota (pode citá-la uma vez, guard-rail
            # no prompt); o schema da chamada 1 continua sem esse campo.
            narrative = self.gemini.narrate(
                {**metrics, "nota_execucao": nota.model_dump(exclude_none=True)},
                student_name=student_name,
            )
        except GeminiError as e:
            warnings.append(f"narrativa indisponível: {e}")
            emit(catalog.ACADEMIA_WARNING, level="warning", error=e, data={"stage": "narrative"})

        if with_audio and narrative:
            try:
                audio_wav = self.gemini.synthesize(narrative)
                audio_b64 = base64.b64encode(audio_wav).decode("ascii")
            except GeminiError as e:
                warnings.append(f"áudio indisponível: {e}")
                emit(catalog.ACADEMIA_WARNING, level="warning", error=e, data={"stage": "audio"})

        persisted_id = self._maybe_persist(student_name, metrics, narrative, persist, warnings,
                                           audio_wav, nota_execucao=nota.model_dump())

        emit(catalog.ACADEMIA_ANALYZE_COMPLETED,
             duration_ms=round((time.monotonic() - t0) * 1000, 1) if t0 else None,
             data={"exercicio": metrics.get("exercicio_identificado"),
                   "veredito": metrics.get("veredito"), "risco_lesao": metrics.get("risco_lesao"),
                   "nota_execucao": nota.nota, "frames_erros": len(frames),
                   "has_narrative": narrative is not None, "has_audio": audio_b64 is not None,
                   "warnings": len(warnings), "persisted_id": persisted_id})
        return AcademiaAnalysisResponse(
            exercicio=analysis.exercicio_identificado,
            metrics=analysis,
            nota_execucao=nota,
            frames_erros=frames,
            narrative=narrative or "",
            audio_base64=audio_b64,
            warnings=warnings,
            persisted_id=str(persisted_id) if persisted_id is not None else None,
        )

    def _maybe_persist(self, student_name, metrics, narrative, persist, warnings,
                       audio_wav: bytes | None = None, nota_execucao: dict | None = None) -> int | None:
        if persist is None:
            persist = self.cfg.academia_persist
        if not persist:
            return None
        try:
            from . import store

            pid = store.save(
                student_name,
                {"metrics": metrics, "nota_execucao": nota_execucao, "narrative": narrative},
                audio_wav,
            )
            if pid is not None:
                emit(catalog.ACADEMIA_PERSISTED, data={
                    "id": pid, "student_name": student_name, "has_audio": audio_wav is not None,
                })
            return pid
        except Exception as e:
            warnings.append(f"persistência falhou (análise não foi salva): {e}")
            emit(catalog.ACADEMIA_WARNING, level="warning", error=e, data={"stage": "persist"})
            return None

    # ----- salva o upload em disco em chunks, validando o tamanho -----
    async def _save_upload(self, upload: UploadFile) -> tuple[str, int]:
        if upload is None or not (upload.filename or "").strip():
            raise EmptyUpload()
        # extensão saneada: evita null-byte/lixo no nome quebrar mkstemp (e o
        # mkstemp já gera o basename aleatório, então não há path traversal)
        fd, path = tempfile.mkstemp(prefix="bitvar_academia_", suffix=_safe_suffix(upload.filename))
        size = 0
        limit = self.cfg.max_upload_bytes
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = await upload.read(self.cfg.academia_upload_chunk_bytes)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise UploadTooLarge(self.cfg.academia_max_upload_mb)
                    out.write(chunk)
        except BaseException:
            _safe_remove(path)
            raise
        if size == 0:
            _safe_remove(path)
            raise EmptyUpload()
        return path, size


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _safe_suffix(filename: str) -> str:
    """Extensão limpa a partir do nome do upload; default '.mp4'."""
    ext = re.sub(r"[^a-z0-9.]", "", os.path.splitext(filename)[1].lower())
    return ext if ext.startswith(".") and 1 < len(ext) <= 10 else ".mp4"
