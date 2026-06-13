"""Orquestrador do pipeline de tênis (blueprint §01).

Junta as cinco etapas: salva o upload em disco em chunks (validando o tamanho),
roteia, chama o Gemini (vídeo→JSON→texto→áudio), calcula o score ponderado no
modo match, persiste opcionalmente e devolve as três saídas. As chamadas de rede
(bloqueantes) rodam em threadpool para não travar o event loop.
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

from .benchmarks import numbers_for
from .config import TennisSettings
from .config import tennis_settings as default_cfg
from .gemini import GeminiError, TennisGemini
from .models import SubjectHint, TennisAnalysisResponse
from .prompts import analysis_system_prompt, build_camera_block, build_subject_block
from .routing import Route, build_route, probe_duration_seconds
from .weights import compute_weighted_score

log = logging.getLogger("bitvar.tennis")


class UploadTooLarge(Exception):
    """Upload excede MAX_UPLOAD_MB."""

    def __init__(self, limit_mb: int):
        super().__init__(f"arquivo acima do limite de {limit_mb} MB")
        self.limit_mb = limit_mb


class EmptyUpload(Exception):
    """Upload vazio ou ausente."""


class TennisService:
    def __init__(self, settings: TennisSettings = default_cfg, gemini: TennisGemini | None = None):
        self.cfg = settings
        self.gemini = gemini or TennisGemini(settings)

    async def analyze_upload(
        self,
        upload: UploadFile,
        *,
        gender: str | None,
        mode_override: str | None = None,
        duration_hint: float | None = None,
        with_audio: bool = True,
        persist: bool | None = None,
        subject: SubjectHint | None = None,
        camera_position: str | None = None,
    ) -> TennisAnalysisResponse:
        t0 = time.monotonic()
        cid = get_correlation_id()
        subject = subject or SubjectHint()
        emit(catalog.TENNIS_ANALYZE_RECEIVED, data={
            "filename": upload.filename, "content_type": upload.content_type,
            "gender": gender, "mode_override": mode_override, "with_audio": with_audio,
            "subject_provided": subject.provided(),
            "subject": subject.model_dump(exclude_none=True),
            "camera_position": camera_position,
        })
        try:
            path, size = await self._save_upload(upload)
        except UploadTooLarge as e:
            emit(catalog.TENNIS_UPLOAD_REJECTED, level="warning", status="error", error=e,
                 data={"reason": "too_large", "limit_mb": self.cfg.max_upload_mb})
            raise
        except EmptyUpload:
            emit(catalog.TENNIS_UPLOAD_REJECTED, level="warning", status="error",
                 data={"reason": "empty"})
            raise
        try:
            emit(catalog.TENNIS_UPLOAD_SAVED, data={"size_mb": round(size / 1024 / 1024, 2)})
            duration = duration_hint or probe_duration_seconds(path)
            route = build_route(
                gender, duration=duration, override=mode_override, file_size_bytes=size
            )
            emit(catalog.TENNIS_ROUTE_DECIDED, data=route.info.model_dump())
            subject_block = build_subject_block(
                subject.name, subject.outfit, subject.side, subject.notes,
                handedness=subject.handedness, headwear=subject.headwear,
                racket_color=subject.racket_color, glasses=subject.glasses,
                hair=subject.hair,
            )
            camera_block = build_camera_block(camera_position)
            route.system_prompt = analysis_system_prompt(
                route.info.gender, route.info.mode, subject_block, camera_block
            )
            mime = upload.content_type or "video/mp4"
            # tudo abaixo é rede bloqueante → threadpool
            return await run_in_threadpool(
                self._run_pipeline, path, mime, route, with_audio, persist, cid, t0,
                subject, camera_position,
            )
        finally:
            _safe_remove(path)

    # ----- pipeline bloqueante (rede) -----
    def _run_pipeline(
        self, path: str, mime: str, route: Route, with_audio: bool,
        persist: bool | None, correlation_id: str | None = None, t0: float | None = None,
        subject: SubjectHint | None = None, camera_position: str | None = None,
    ) -> TennisAnalysisResponse:
        # re-fixa o contexto no worker thread (correlaciona os eventos do Gemini)
        set_context(correlation_id=correlation_id)
        subject = subject or SubjectHint()
        warnings: list[str] = []
        gender, mode = route.info.gender, route.info.mode

        file = self.gemini.upload_video(path, mime_type=mime)
        try:
            analysis = self.gemini.analyze(
                file,
                schema_model=route.schema_model,
                system_prompt=route.system_prompt,
                fps=route.info.fps,
                media_resolution=route.info.media_resolution,
            )
        finally:
            self.gemini.delete_file(file)

        metrics = analysis.model_dump(by_alias=True, exclude_none=True)
        metrics.setdefault("analysis_mode", mode)
        metrics.setdefault("gender_profile", gender)
        if camera_position:  # metadado da câmera-base (spec B1)
            metrics["camera_meta"] = {"position": camera_position}

        if mode == "match" and route.weight_model:
            try:
                ws = compute_weighted_score(metrics, route.weight_model)
                metrics["weighted_performance_score"] = ws
                emit(catalog.TENNIS_WEIGHTED_SCORE, data={
                    "score": ws["score"], "model": ws["weighting_model"],
                    "components_present": ws["components_present"],
                })
            except Exception as e:  # não derruba a análise por causa do score
                warnings.append(f"score ponderado não calculado: {e}")
                emit(catalog.TENNIS_WARNING, level="warning", error=e, data={"stage": "weighted_score"})

        narrative: str | None = None
        audio_b64: str | None = None
        audio_wav: bytes | None = None
        try:
            narrative = self.gemini.narrate(metrics, gender=gender, mode=mode, player_name=subject.name)
        except GeminiError as e:
            warnings.append(f"narrativa indisponível: {e}")
            emit(catalog.TENNIS_WARNING, level="warning", error=e, data={"stage": "narrative"})

        if with_audio and narrative:
            try:
                audio_wav = self.gemini.synthesize(narrative)
                audio_b64 = base64.b64encode(audio_wav).decode("ascii")
            except GeminiError as e:
                warnings.append(f"áudio indisponível: {e}")
                emit(catalog.TENNIS_WARNING, level="warning", error=e, data={"stage": "audio"})

        persisted_id = self._maybe_persist(
            gender, mode, metrics, narrative, persist, warnings, audio_wav
        )

        emit(catalog.TENNIS_ANALYZE_COMPLETED,
             duration_ms=round((time.monotonic() - t0) * 1000, 1) if t0 else None,
             data={"mode": mode, "gender": gender, "shot": metrics.get("shot_identified"),
                   "has_narrative": narrative is not None, "has_audio": audio_b64 is not None,
                   "warnings": len(warnings), "persisted_id": persisted_id})
        return TennisAnalysisResponse(
            route=route.info,
            subject=subject if subject.provided() else None,
            metrics=metrics,
            benchmarks=numbers_for(gender),
            narrative=narrative,
            audio_base64=audio_b64,
            warnings=warnings,
            persisted_id=persisted_id,
        )

    def _maybe_persist(self, gender, mode, metrics, narrative, persist, warnings,
                       audio_wav: bytes | None = None) -> int | None:
        if persist is None:
            persist = self.cfg.tennis_persist
        if not persist:
            return None
        try:
            from . import store

            pid = store.save(
                gender, mode, {"metrics": metrics, "narrative": narrative}, audio_wav
            )
            if pid is not None:
                emit(catalog.TENNIS_PERSISTED, data={
                    "id": pid, "mode": mode, "gender": gender, "has_audio": audio_wav is not None,
                })
            return pid
        except Exception as e:
            warnings.append(f"persistência falhou (análise não foi salva): {e}")
            emit(catalog.TENNIS_WARNING, level="warning", error=e, data={"stage": "persist"})
            return None

    # ----- salva o upload em disco em chunks, validando o tamanho -----
    async def _save_upload(self, upload: UploadFile) -> tuple[str, int]:
        if upload is None or not (upload.filename or "").strip():
            raise EmptyUpload()
        # extensão saneada: evita null-byte/lixo no nome quebrar mkstemp (e o
        # mkstemp já gera o basename aleatório, então não há path traversal)
        fd, path = tempfile.mkstemp(prefix="bitvar_tennis_", suffix=_safe_suffix(upload.filename))
        size = 0
        limit = self.cfg.max_upload_bytes
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = await upload.read(self.cfg.upload_chunk_bytes)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise UploadTooLarge(self.cfg.max_upload_mb)
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
