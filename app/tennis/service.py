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
from .quadrants import build_quadrant_block, quadrant_frame_side, quadrant_label
from .routing import Route, build_route, probe_duration_seconds
from .rules import build_rules_block
from .weights import compute_clip_weighted_score, compute_weighted_score

log = logging.getLogger("bitvar.tennis")


class UploadTooLarge(Exception):
    """Upload excede MAX_UPLOAD_MB."""

    def __init__(self, limit_mb: int):
        super().__init__(f"arquivo acima do limite de {limit_mb} MB")
        self.limit_mb = limit_mb


class EmptyUpload(Exception):
    """Upload vazio ou ausente."""


class ClipTooLong(Exception):
    """Clipe acima da duração máxima (``clip_max_seconds``). Modo partida foi removido."""

    def __init__(self, limit_s: float, duration: float | None = None):
        super().__init__(f"clipe acima do limite de {limit_s:.0f}s — envie um lance mais curto.")
        self.limit_s = limit_s
        self.duration = duration


class TennisService:
    def __init__(self, settings: TennisSettings = default_cfg, gemini: TennisGemini | None = None):
        self.cfg = settings
        self.gemini = gemini or TennisGemini(settings)

    async def analyze_upload(
        self,
        upload: UploadFile,
        *,
        gender: str | None,
        level: str | None = None,
        mode_override: str | None = None,
        duration_hint: float | None = None,
        with_audio: bool = True,
        persist: bool | None = None,
        subject: SubjectHint | None = None,
        camera_position: str | None = None,
        target_quadrant: object = None,
        target_appearance: str | None = None,
    ) -> TennisAnalysisResponse:
        t0 = time.monotonic()
        cid = get_correlation_id()
        subject = subject or SubjectHint()
        emit(catalog.TENNIS_ANALYZE_RECEIVED, data={
            "filename": upload.filename, "content_type": upload.content_type,
            "gender": gender, "level": level, "mode_override": mode_override,
            "with_audio": with_audio,
            "subject_provided": subject.provided(),
            "subject": subject.model_dump(exclude_none=True),
            "camera_position": camera_position,
            "target_quadrant": target_quadrant,
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
            # teto de duração do clipe (modo partida removido): rejeita se a duração
            # for conhecida e exceder o limite. Duração indeterminável → segue (o
            # servidor não tem como aferir; degradação graciosa).
            if duration is not None and duration > self.cfg.clip_max_seconds:
                emit(catalog.TENNIS_UPLOAD_REJECTED, level="warning", status="error",
                     data={"reason": "too_long", "duration_s": round(duration, 1),
                           "limit_s": self.cfg.clip_max_seconds})
                raise ClipTooLong(self.cfg.clip_max_seconds, duration)
            route = build_route(
                gender, duration=duration, override=mode_override,
                file_size_bytes=size, level_in=level,
                camera_reference=camera_position,
                target_quadrant=target_quadrant,
                target_appearance=target_appearance,
            )
            emit(catalog.TENNIS_ROUTE_DECIDED, data=route.info.model_dump())
            quadrant_active = route.info.target_quadrant is not None
            subject_block = build_subject_block(
                subject.name, subject.outfit, subject.side, subject.notes,
                handedness=subject.handedness, headwear=subject.headwear,
                racket_color=subject.racket_color, glasses=subject.glasses,
                hair=subject.hair, quadrant_active=quadrant_active,
            )
            # com quadrante ativo a câmera é lida RELATIVA AO FRAME (sem flip 'central')
            camera_block = build_camera_block(camera_position, frame_relative=quadrant_active)
            # âncora geométrica do alvo por quadrante (precede a aparência). A cor de
            # continuidade vem de target_appearance ou, na falta, da roupa do subject. O
            # mapa do quadrante (canto/lado/leitura) muda com a CÂMERA — o canto inferior-
            # esquerdo é Q3 de fundo, Q2 na lateral (spec quadrante×câmera 25/06).
            quadrant_block = build_quadrant_block(
                route.info.target_quadrant,
                route.info.target_appearance or subject.outfit,
                camera=route.info.camera_reference,
            )
            # regras táticas cobráveis para a categoria (gênero × nível já normalizados)
            rules_block = build_rules_block(route.info.gender, route.info.level)
            route.system_prompt = analysis_system_prompt(
                route.info.gender, route.info.mode, subject_block, camera_block, rules_block,
                fps=route.info.fps, quadrant_block=quadrant_block,
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

        # AUTO-CHECK DE SETOR (plano 25/06): compara o setor de SAÍDA (onde a IA diz
        # que o atleta analisado começou) com o QUADRANTE de ENTRADA. Divergiu → marca
        # 'target_mismatch' e vira AVISO no laudo — nunca erro. Fecha o loop "travou no
        # atleta certo?" antes de ir pro Juca.
        mismatch = _check_target_sector(
            metrics, route.info.target_quadrant, route.info.camera_reference
        )
        if mismatch:
            metrics["target_mismatch"] = mismatch
            warnings.append(mismatch["message"])
            emit(catalog.TENNIS_WARNING, level="warning",
                 data={"stage": "target_sector_check", **mismatch})

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
        elif mode == "clip":
            try:
                phase = metrics.get("action_phase")
                cws = compute_clip_weighted_score(metrics, phase, gender)
                # nota OFICIAL calibrável em Python (condicionada à fase);
                # clip_quality_score do VLM segue como referência qualitativa.
                metrics["weighted_performance_score"] = cws
                if cws.get("axis_incomplete"):  # eixo dominante da fase sem nenhum dado
                    warnings.append(cws["note"])
                emit(catalog.TENNIS_WEIGHTED_SCORE, data={
                    "score": cws["score"], "model": cws["weighting_model"],
                    "components_present": cws["components_present"], "phase": phase,
                })
            except Exception as e:  # nunca derruba a análise por causa do score
                warnings.append(f"score ponderado (clip) não calculado: {e}")
                emit(catalog.TENNIS_WARNING, level="warning", error=e, data={"stage": "weighted_score_clip"})

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


def _check_target_sector(
    metrics: dict, quadrant: int | None, camera: object = None
) -> dict | None:
    """Auto-verificação de setor: o atleta analisado começou no LADO do quadrante?

    Compara ``positioning.observed_side`` (setor de saída da IA) com o lado de IMAGEM
    do quadrante de entrada (:func:`app.tennis.quadrants.quadrant_frame_side`) — que é
    CAMERA-DEPENDENTE: o mesmo número aponta lados diferentes de fundo × lateral, então
    a ``camera`` tem de entrar aqui ou o auto-check daria falso alarme na lateral. Só
    acusa divergência quando os DOIS lados são definidos e OPOSTOS (esquerda × direita):
    ``"centro"`` ou ausência são inconclusivos — não disparam alarme falso. Retorna um
    dict de aviso (``message`` + campos para auditoria) ou ``None``. Nunca levanta:
    auditar a trava no alvo jamais pode derrubar a análise.
    """
    expected = quadrant_frame_side(quadrant, camera)
    if not expected:
        return None
    pos = metrics.get("positioning") or {}
    observed = pos.get("observed_side")
    if observed not in ("esquerda", "direita") or observed == expected:
        return None  # inconclusivo (centro/ausente) ou bateu → sem aviso
    label = quadrant_label(quadrant, camera) or f"Q{quadrant}"
    return {
        "target_quadrant": quadrant,
        "expected_side": expected,
        "observed_side": observed,
        "message": (
            f"possível atleta errado: você apontou o quadrante {quadrant} ({label}, "
            f"lado {expected} do quadro), mas a IA situou o atleta analisado no lado "
            f"{observed} do quadro. Pode ter pego o parceiro/adversário — confira o vídeo."
        ),
    }


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _safe_suffix(filename: str) -> str:
    """Extensão limpa a partir do nome do upload; default '.mp4'."""
    ext = re.sub(r"[^a-z0-9.]", "", os.path.splitext(filename)[1].lower())
    return ext if ext.startswith(".") and 1 < len(ext) <= 10 else ".mp4"
