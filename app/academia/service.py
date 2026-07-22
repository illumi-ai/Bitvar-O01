"""Orquestra o pipeline da vertical Academia.

O serviço mantém as invariantes de segurança do produto: upload em chunks,
formato e duração validados, arquivo local/remoto removido, rede síncrona no
threadpool e persistência best-effort. O VLM produz observações estruturadas;
score, cobertura da metodologia e gate de captura são decisões em Python.
"""

from __future__ import annotations

import asyncio
import base64
import math
import os
import re
import tempfile
import time
import unicodedata
import wave

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError

from app.events import catalog, emit, get_correlation_id, set_context

from .audio import (
    AudioNormalizationError,
    AudioProcessingUnavailable,
    audio_tools_available,
    normalize_audio_content_type,
    normalize_audio_to_wav,
    probe_audio_duration_seconds,
    safe_audio_suffix,
    supported_audio_upload,
)
from .config import AcademiaSettings
from .config import academia_settings as default_cfg
from .gemini import AcademiaGemini, GeminiError
from .media import detect_video_mime, probe_duration_seconds, safe_video_suffix
from .models import (
    AcademiaAnalysisResponse,
    AnalysisStatus,
    CriterionAssessment,
    ExerciseIdentification,
    ExerciseIdentificationPass,
    GeneralExecutionAnalysis,
    GeneralExecutionCapturePass,
    GeneralExecutionChecklistPass,
    PractitionerHint,
    TargetDescriptionTranscriptionResponse,
    WeightedExecutionScore,
)
from .profiles import (
    EXERCISE_FAMILY_LABELS,
    ExerciseProfile,
    profile_for_family,
)
from .prompts import (
    build_analysis_prompt,
    build_general_analysis_prompt,
    build_identification_prompt,
)
from .routing import AcademiaRoute, build_route, normalize_capture_angle
from .scoring import compute_execution_score


class UploadTooLarge(Exception):
    def __init__(self, limit_mb: int):
        super().__init__(f"arquivo acima do limite de {limit_mb} MB")
        self.limit_mb = limit_mb


class EmptyUpload(Exception):
    pass


class InvalidVideo(Exception):
    pass


class VideoTooLong(Exception):
    def __init__(self, limit_s: float, duration: float):
        super().__init__(
            f"vídeo acima do limite de {limit_s:.0f}s — envie somente uma série."
        )
        self.limit_s = limit_s
        self.duration = duration


class UnverifiableVideoDuration(Exception):
    def __init__(self):
        super().__init__(
            "não foi possível aferir a duração do vídeo no servidor; "
            "reencode o arquivo e tente novamente."
        )


class AnalysisBusy(Exception):
    pass


class VoiceTranscriptionBusy(Exception):
    pass


class EmptyVoiceAudio(Exception):
    pass


class VoiceAudioTooLarge(Exception):
    def __init__(self, limit_mb: int):
        super().__init__(f"gravação acima do limite de {limit_mb} MB")
        self.limit_mb = limit_mb


class VoiceAudioTooLong(Exception):
    def __init__(self, limit_s: float, duration: float):
        super().__init__(
            f"gravação acima do limite de {limit_s:.0f}s; grave uma descrição mais curta."
        )
        self.limit_s = limit_s
        self.duration = duration


class InvalidVoiceAudio(Exception):
    pass


class VoiceTranscriptionUnavailable(Exception):
    pass


class NoSpeechDetected(Exception):
    pass


class AcademiaService:
    def __init__(
        self,
        settings: AcademiaSettings = default_cfg,
        gemini: AcademiaGemini | None = None,
    ):
        self.cfg = settings
        self.gemini = gemini or AcademiaGemini(settings)
        self._slots = asyncio.Semaphore(settings.academia_max_concurrent_analyses)
        self._transcription_slots = asyncio.Semaphore(
            settings.academia_max_concurrent_transcriptions
        )

    async def transcribe_target_upload(
        self,
        upload: UploadFile,
    ) -> TargetDescriptionTranscriptionResponse:
        """Transcreve uma descrição curta sem persistir áudio ou texto."""
        try:
            await asyncio.wait_for(
                self._transcription_slots.acquire(),
                timeout=2.0,
            )
        except TimeoutError as exc:
            raise VoiceTranscriptionBusy(
                "o transcritor está ocupado; tente novamente em alguns instantes."
            ) from exc
        try:
            return await self._transcribe_target_with_slot(upload)
        finally:
            self._transcription_slots.release()

    async def _transcribe_target_with_slot(
        self,
        upload: UploadFile,
    ) -> TargetDescriptionTranscriptionResponse:
        started = time.monotonic()
        correlation_id = get_correlation_id()
        declared_mime = normalize_audio_content_type(
            getattr(upload, "content_type", None)
        )
        emit(
            catalog.ACADEMIA_TRANSCRIPTION_RECEIVED,
            data={"declared_mime": declared_mime or "unknown"},
        )
        path: str | None = None
        try:
            if not audio_tools_available():
                raise VoiceTranscriptionUnavailable(
                    "transcrição de voz indisponível neste servidor."
                )
            path, size, normalized_mime = await self._save_voice_upload(upload)
            duration_hint = await run_in_threadpool(
                probe_audio_duration_seconds,
                path,
            )
            if (
                duration_hint is not None
                and duration_hint > self.cfg.academia_voice_max_seconds
            ):
                raise VoiceAudioTooLong(
                    self.cfg.academia_voice_max_seconds,
                    duration_hint,
                )
            return await run_in_threadpool(
                self._run_voice_transcription,
                path,
                size,
                normalized_mime,
                correlation_id,
                started,
            )
        except (
            EmptyVoiceAudio,
            VoiceAudioTooLarge,
            VoiceAudioTooLong,
            InvalidVoiceAudio,
            VoiceTranscriptionUnavailable,
            NoSpeechDetected,
        ) as exc:
            emit(
                catalog.ACADEMIA_TRANSCRIPTION_FAILED,
                level="warning",
                status="error",
                data={
                    "reason": _voice_failure_reason(exc),
                    "error_type": type(exc).__name__,
                },
            )
            raise
        except GeminiError as exc:
            emit(
                catalog.ACADEMIA_TRANSCRIPTION_FAILED,
                level="error",
                status="error",
                error=exc,
                data={"reason": "gemini"},
            )
            raise
        finally:
            if path:
                _safe_remove_voice(path, phase="source")

    def _run_voice_transcription(
        self,
        source_path: str,
        source_size: int,
        source_mime: str,
        correlation_id: str | None,
        started: float,
    ) -> TargetDescriptionTranscriptionResponse:
        set_context(correlation_id=correlation_id)
        descriptor, wav_path = tempfile.mkstemp(
            prefix="bitvar_academia_voice_normalized_",
            suffix=".wav",
        )
        os.close(descriptor)
        try:
            try:
                normalize_audio_to_wav(
                    source_path,
                    wav_path,
                    max_seconds=self.cfg.academia_voice_max_seconds,
                    timeout_seconds=(
                        self.cfg.academia_voice_transcode_timeout_seconds
                    ),
                )
            except AudioProcessingUnavailable as exc:
                raise VoiceTranscriptionUnavailable(
                    "processamento de áudio temporariamente indisponível."
                ) from exc
            except AudioNormalizationError as exc:
                raise InvalidVoiceAudio(str(exc)) from exc
            try:
                with wave.open(wav_path, "rb") as normalized:
                    frame_rate = normalized.getframerate()
                    frame_count = normalized.getnframes()
                    if (
                        normalized.getcomptype() != "NONE"
                        or normalized.getnchannels() != 1
                        or normalized.getsampwidth() != 2
                        or frame_rate != 16000
                        or frame_count <= 0
                    ):
                        raise InvalidVoiceAudio(
                            "o áudio normalizado não possui o formato esperado."
                        )
                    duration = frame_count / frame_rate
                if duration > self.cfg.academia_voice_max_seconds:
                    raise VoiceAudioTooLong(
                        self.cfg.academia_voice_max_seconds,
                        duration,
                    )
                with open(wav_path, "rb") as stream:
                    audio_wav = stream.read()
            except (OSError, wave.Error) as exc:
                raise InvalidVoiceAudio(
                    "não foi possível ler a gravação preparada."
                ) from exc
            transcript = self.gemini.transcribe_target_description(audio_wav)
            if not transcript:
                raise NoSpeechDetected(
                    "não foi identificada fala clara; grave novamente ou digite a descrição."
                )
            transcript, truncated = _limit_voice_transcript(transcript, 500)
            response = TargetDescriptionTranscriptionResponse(
                transcript=transcript,
                duration_seconds=round(duration, 3),
                truncated=truncated,
            )
            emit(
                catalog.ACADEMIA_TRANSCRIPTION_COMPLETED,
                duration_ms=round((time.monotonic() - started) * 1000, 1),
                data={
                    "source_bytes": source_size,
                    "declared_mime": source_mime,
                    "duration_seconds": round(duration, 3),
                    "chars": len(response.transcript),
                    "truncated": truncated,
                },
            )
            return response
        finally:
            _safe_remove_voice(wav_path, phase="normalized")

    async def analyze_upload(
        self,
        upload: UploadFile,
        *,
        practitioner: PractitionerHint | None = None,
        capture_angle: str | None = None,
        duration_hint: float | None = None,
        with_audio: bool = True,
        persist: bool | None = None,
    ) -> AcademiaAnalysisResponse:
        """Executa mantendo no máximo N análises caras concorrentes."""
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=2.0)
        except TimeoutError as exc:
            raise AnalysisBusy(
                "o analisador está ocupado; tente novamente em alguns instantes."
            ) from exc
        try:
            return await self._analyze_with_slot(
                upload,
                practitioner=practitioner,
                capture_angle=capture_angle,
                duration_hint=duration_hint,
                with_audio=with_audio,
                persist=persist,
            )
        finally:
            self._slots.release()

    async def _analyze_with_slot(
        self,
        upload: UploadFile,
        *,
        practitioner: PractitionerHint | None,
        capture_angle: str | None,
        duration_hint: float | None,
        with_audio: bool,
        persist: bool | None,
    ) -> AcademiaAnalysisResponse:
        started = time.monotonic()
        correlation_id = get_correlation_id()
        practitioner = practitioner or PractitionerHint()
        # Não enviar filename, nome ou demais descritores pessoais ao log/evento.
        emit(
            catalog.ACADEMIA_ANALYZE_RECEIVED,
            data={
                "content_type": upload.content_type,
                "identification_mode": "automatic",
                "capture_angle": capture_angle,
                "with_audio": with_audio,
                "practitioner_hint": practitioner.provided(),
            },
        )
        normalized_capture_angle = normalize_capture_angle(capture_angle)
        path: str | None = None
        try:
            path, size, detected_mime = await self._save_upload(upload)
            emit(
                catalog.ACADEMIA_UPLOAD_SAVED,
                data={
                    "size_mb": round(size / 1024 / 1024, 2),
                    "mime": detected_mime,
                },
            )
            # O valor do navegador é apenas metadado de UX e nunca autoriza o
            # upload: o teto depende exclusivamente da aferição server-side.
            if duration_hint is not None and (
                not math.isfinite(duration_hint) or duration_hint <= 0
            ):
                raise ValueError("duration_seconds deve ser um número positivo.")
            duration = await run_in_threadpool(probe_duration_seconds, path)
            if duration is None:
                raise UnverifiableVideoDuration()
            if duration is not None and duration > self.cfg.academia_video_max_seconds:
                raise VideoTooLong(self.cfg.academia_video_max_seconds, duration)
            return await run_in_threadpool(
                self._run_pipeline,
                path,
                detected_mime,
                duration,
                normalized_capture_angle,
                practitioner,
                with_audio,
                persist,
                correlation_id,
                started,
            )
        except (
            UploadTooLarge,
            EmptyUpload,
            InvalidVideo,
            VideoTooLong,
            UnverifiableVideoDuration,
        ) as exc:
            reason = {
                UploadTooLarge: "too_large",
                EmptyUpload: "empty",
                InvalidVideo: "invalid_format",
                VideoTooLong: "too_long",
                UnverifiableVideoDuration: "duration_unverifiable",
            }.get(type(exc), "invalid")
            emit(
                catalog.ACADEMIA_UPLOAD_REJECTED,
                level="warning",
                status="error",
                error=exc,
                data={"reason": reason},
            )
            raise
        finally:
            if path:
                _safe_remove(path)

    def _run_pipeline(
        self,
        path: str,
        mime: str,
        duration: float,
        capture_angle: str,
        practitioner: PractitionerHint,
        with_audio: bool,
        persist: bool | None,
        correlation_id: str | None,
        started: float,
    ) -> AcademiaAnalysisResponse:
        set_context(correlation_id=correlation_id)
        warnings: list[str] = []
        narrative_name = _safe_narrative_name(practitioner.name)

        remote_file = self.gemini.upload_video(path, mime_type=mime)
        try:
            raw_identification = self.gemini.identify(
                remote_file,
                system_prompt=build_identification_prompt(
                    practitioner=practitioner,
                    duration_seconds=duration,
                ),
                fps=self.cfg.academia_identification_fps,
                media_resolution=self.cfg.academia_identification_media_resolution,
            )
            identification = _materialize_identification(
                raw_identification,
                duration_seconds=duration,
            )
            _emit_identification(identification)

            if identification.reason not in {"supported", "general_supported"}:
                return _identification_only_response(
                    identification,
                    practitioner=practitioner,
                    started=started,
                )

            profile = profile_for_family(identification.exercise_family)
            if profile is None:  # defesa em profundidade contra registro inconsistente
                raise GeminiError(
                    "a identificação apontou uma metodologia que não está registrada."
                )
            route = build_route(
                profile,
                duration=duration,
                capture_angle=capture_angle,
                settings=self.cfg,
            )
            emit(
                catalog.ACADEMIA_PROFILE_SELECTED,
                data={
                    "exercise": profile.slug,
                    "methodology_version": profile.methodology_version,
                    "methodology_status": profile.methodology_status,
                    "methodology_scope": route.info.methodology_scope,
                    "capture_angle": route.info.capture_angle,
                },
            )
            if identification.reason == "general_supported":
                route.system_prompt = build_general_analysis_prompt(
                    profile,
                    capture_angle=route.info.capture_angle,
                    practitioner=practitioner,
                    fps=route.info.fps,
                )
                return self._run_general_analysis(
                    remote_file,
                    route=route,
                    profile=profile,
                    identification=identification,
                    practitioner=practitioner,
                    narrative_name=narrative_name,
                    with_audio=with_audio,
                    persist=persist,
                    warnings=warnings,
                    started=started,
                )
            route.system_prompt = build_analysis_prompt(
                profile,
                capture_angle=route.info.capture_angle,
                practitioner=practitioner,
                fps=route.info.fps,
            )
            return self._run_supported_analysis(
                remote_file,
                route=route,
                profile=profile,
                identification=identification,
                practitioner=practitioner,
                narrative_name=narrative_name,
                with_audio=with_audio,
                persist=persist,
                warnings=warnings,
                started=started,
            )
        finally:
            self.gemini.delete_file(remote_file)

    def _run_supported_analysis(
        self,
        remote_file,
        *,
        route: AcademiaRoute,
        profile: ExerciseProfile,
        identification: ExerciseIdentification,
        practitioner: PractitionerHint,
        narrative_name: str | None,
        with_audio: bool,
        persist: bool | None,
        warnings: list[str],
        started: float,
    ) -> AcademiaAnalysisResponse:
        raw_analysis = self.gemini.analyze(
            remote_file,
            schema_model=route.schema_model,
            system_prompt=route.system_prompt,
            fps=route.info.fps,
            media_resolution=route.info.media_resolution,
            duration_seconds=route.info.duration_seconds,
            active_start_seconds=(
                identification.active_interval.start_s
                if identification.active_interval
                else None
            ),
            active_end_seconds=(
                identification.active_interval.end_s
                if identification.active_interval
                else None
            ),
        )

        metrics = raw_analysis.model_dump(exclude_none=True)
        _normalize_methodology(metrics, profile, warnings)
        _sanitize_repetitions(metrics, route.info.duration_seconds, warnings)
        _canonicalize_generated_text(metrics, profile)
        _derive_constructive_summary(metrics)
        analysis_status = _decide_analysis_status(metrics, profile)
        if analysis_status == "recapture_required":
            _suppress_unreliable_assessment(metrics)

        score = WeightedExecutionScore.model_validate(
            compute_execution_score(metrics, profile)
        )
        metrics["weighted_execution_score"] = score.model_dump()
        emit(
            catalog.ACADEMIA_SCORE_COMPUTED,
            data={
                "score": score.score,
                "coverage": score.coverage,
                "model": score.weighting_model,
                "valid": score.valid,
            },
        )

        if analysis_status == "recapture_required":
            warning = "captura inadequada: grave novamente antes de interpretar a execução."
            warnings.append(warning)
            emit(
                catalog.ACADEMIA_CAPTURE_REJECTED,
                level="warning",
                data={"issues": len(metrics.get("capture_quality", {}).get("issues", []))},
            )
            narrative = _recapture_report(metrics, profile)
            emit(
                catalog.ACADEMIA_REPORT_GENERATED,
                data={
                    "status": analysis_status,
                    "kind": "recapture",
                    "chars": len(narrative),
                },
            )
        else:
            emit(
                catalog.ACADEMIA_CAPTURE_ACCEPTED,
                level="warning" if analysis_status == "limited" else "info",
                data={
                    "status": analysis_status,
                    "complete_repetitions": metrics.get("movement", {}).get(
                        "complete_repetitions", 0
                    ),
                    "score_coverage": score.coverage,
                },
            )
            narrative = None
            try:
                narrative = self.gemini.narrate(
                    metrics,
                    practitioner_name=narrative_name,
                    analysis_status=analysis_status,
                )
                safety_issue = _narrative_safety_issue(narrative)
                if safety_issue:
                    warnings.append(
                        "narrativa generativa descartada por segurança; foi usado um relatório textual local."
                    )
                    emit(
                        catalog.ACADEMIA_WARNING,
                        level="warning",
                        data={"stage": "narrative_safety", "reason": safety_issue},
                    )
                    narrative = _fallback_report(
                        metrics,
                        practitioner_name=narrative_name,
                        limited=analysis_status == "limited",
                        exercise_label=profile.label,
                    )
                    emit(
                        catalog.ACADEMIA_REPORT_GENERATED,
                        data={
                            "status": analysis_status,
                            "kind": "safety_fallback",
                            "chars": len(narrative),
                        },
                    )
                else:
                    emit(
                        catalog.ACADEMIA_REPORT_GENERATED,
                        data={"status": analysis_status, "chars": len(narrative)},
                    )
            except GeminiError as exc:
                warnings.append(
                    "narrativa do Gemini indisponível; foi usado um relatório textual local."
                )
                narrative = _fallback_report(
                    metrics,
                    practitioner_name=narrative_name,
                    limited=analysis_status == "limited",
                    exercise_label=profile.label,
                )
                emit(
                    catalog.ACADEMIA_WARNING,
                    level="warning",
                    error=exc,
                    data={"stage": "narrative"},
                )
                emit(
                    catalog.ACADEMIA_REPORT_GENERATED,
                    data={
                        "status": analysis_status,
                        "kind": "deterministic_fallback",
                        "chars": len(narrative),
                    },
                )

        audio_wav: bytes | None = None
        audio_base64: str | None = None
        if with_audio and narrative:
            try:
                audio_wav = self.gemini.synthesize(narrative)
                audio_base64 = base64.b64encode(audio_wav).decode("ascii")
            except GeminiError as exc:
                warnings.append("áudio indisponível nesta análise.")
                emit(
                    catalog.ACADEMIA_WARNING,
                    level="warning",
                    error=exc,
                    data={"stage": "audio"},
                )

        try:
            validated_metrics = profile.schema_model.model_validate(metrics)
        except ValidationError as exc:
            # A entrada HTTP já foi validada; aqui o dado inválido veio da saída
            # multimodal/pós-processamento e deve ser tratado como falha do pipeline.
            raise GeminiError("resultado estruturado da análise ficou inválido.") from exc
        persisted_id = self._maybe_persist(
            route,
            profile,
            identification,
            practitioner,
            analysis_status,
            validated_metrics,
            narrative,
            warnings,
            audio_wav,
            persist,
        )
        emit(
            catalog.ACADEMIA_ANALYZE_COMPLETED,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            data={
                "exercise": profile.slug,
                "methodology_version": profile.methodology_version,
                "analysis_status": analysis_status,
                "has_narrative": narrative is not None,
                "has_audio": audio_wav is not None,
                "warnings": len(warnings),
                "persisted": persisted_id is not None,
            },
        )
        return AcademiaAnalysisResponse(
            analysis_status=analysis_status,
            identification=identification,
            route=route.info,
            practitioner=practitioner if practitioner.provided() else None,
            metrics=validated_metrics.model_dump(),
            narrative=narrative,
            audio_base64=audio_base64,
            warnings=warnings,
            persisted_id=persisted_id,
        )

    def _run_general_analysis(
        self,
        remote_file,
        *,
        route: AcademiaRoute,
        profile: ExerciseProfile,
        identification: ExerciseIdentification,
        practitioner: PractitionerHint,
        narrative_name: str | None,
        with_audio: bool,
        persist: bool | None,
        warnings: list[str],
        started: float,
    ) -> AcademiaAnalysisResponse:
        """Executa a metodologia observacional geral sem publicar nota numérica."""

        active_start = (
            identification.active_interval.start_s
            if identification.active_interval
            else None
        )
        active_end = (
            identification.active_interval.end_s
            if identification.active_interval
            else None
        )
        capture, checklist = self.gemini.analyze_general(
            remote_file,
            system_prompt=route.system_prompt,
            fps=route.info.fps,
            media_resolution=route.info.media_resolution,
            duration_seconds=route.info.duration_seconds,
            active_start_seconds=active_start,
            active_end_seconds=active_end,
        )
        metrics = _materialize_general_analysis(
            capture,
            checklist,
            identification=identification,
            profile=profile,
            duration_seconds=route.info.duration_seconds,
            timeline_offset_s=active_start or 0.0,
            active_end_s=active_end,
            warnings=warnings,
        )
        analysis_status = _decide_general_analysis_status(metrics, profile)
        if analysis_status == "recapture_required":
            _suppress_unreliable_general_assessment(metrics)
            warning = (
                "captura inadequada: grave novamente antes de interpretar a execução."
            )
            warnings.append(warning)
            emit(
                catalog.ACADEMIA_CAPTURE_REJECTED,
                level="warning",
                data={
                    "issues": len(
                        metrics.get("capture_quality", {}).get("issues", [])
                    ),
                    "methodology_scope": "general_execution",
                },
            )
            narrative = _general_recapture_report(metrics, profile)
            emit(
                catalog.ACADEMIA_REPORT_GENERATED,
                data={
                    "status": analysis_status,
                    "kind": "general_recapture",
                    "chars": len(narrative),
                },
            )
        else:
            reliability = (metrics.get("execution_summary") or {}).get(
                "reliability",
                {},
            )
            emit(
                catalog.ACADEMIA_CAPTURE_ACCEPTED,
                level="warning" if analysis_status == "limited" else "info",
                data={
                    "status": analysis_status,
                    "complete_repetitions": metrics.get("movement", {}).get(
                        "complete_repetitions",
                        0,
                    ),
                    "coverage": reliability.get("coverage", 0),
                    "reliability": reliability.get("level", "baixa"),
                    "methodology_scope": "general_execution",
                },
            )
            try:
                narrative = self.gemini.narrate(
                    metrics,
                    practitioner_name=narrative_name,
                    analysis_status=analysis_status,
                )
                safety_issue = _narrative_safety_issue(narrative)
                if safety_issue:
                    warnings.append(
                        "narrativa generativa descartada por segurança; "
                        "foi usado um relatório textual local."
                    )
                    emit(
                        catalog.ACADEMIA_WARNING,
                        level="warning",
                        data={
                            "stage": "general_narrative_safety",
                            "reason": safety_issue,
                        },
                    )
                    narrative = _general_fallback_report(
                        metrics,
                        practitioner_name=narrative_name,
                        limited=analysis_status == "limited",
                    )
                    report_kind = "general_safety_fallback"
                else:
                    report_kind = "general_generated"
            except GeminiError as exc:
                warnings.append(
                    "narrativa do Gemini indisponível; "
                    "foi usado um relatório textual local."
                )
                emit(
                    catalog.ACADEMIA_WARNING,
                    level="warning",
                    error=exc,
                    data={"stage": "general_narrative"},
                )
                narrative = _general_fallback_report(
                    metrics,
                    practitioner_name=narrative_name,
                    limited=analysis_status == "limited",
                )
                report_kind = "general_deterministic_fallback"
            emit(
                catalog.ACADEMIA_REPORT_GENERATED,
                data={
                    "status": analysis_status,
                    "kind": report_kind,
                    "chars": len(narrative),
                },
            )

        audio_wav: bytes | None = None
        audio_base64: str | None = None
        if with_audio and narrative:
            try:
                audio_wav = self.gemini.synthesize(narrative)
                audio_base64 = base64.b64encode(audio_wav).decode("ascii")
            except GeminiError as exc:
                warnings.append("áudio indisponível nesta análise.")
                emit(
                    catalog.ACADEMIA_WARNING,
                    level="warning",
                    error=exc,
                    data={"stage": "general_audio"},
                )

        try:
            validated_metrics = GeneralExecutionAnalysis.model_validate(metrics)
        except ValidationError as exc:
            raise GeminiError(
                "resultado estruturado da análise geral ficou inválido."
            ) from exc
        persisted_id = self._maybe_persist(
            route,
            profile,
            identification,
            practitioner,
            analysis_status,
            validated_metrics,
            narrative,
            warnings,
            audio_wav,
            persist,
        )
        emit(
            catalog.ACADEMIA_ANALYZE_COMPLETED,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            data={
                "exercise": profile.slug,
                "methodology_version": profile.methodology_version,
                "methodology_scope": "general_execution",
                "analysis_status": analysis_status,
                "has_narrative": narrative is not None,
                "has_audio": audio_wav is not None,
                "warnings": len(warnings),
                "persisted": persisted_id is not None,
            },
        )
        return AcademiaAnalysisResponse(
            analysis_status=analysis_status,
            identification=identification,
            route=route.info,
            practitioner=practitioner if practitioner.provided() else None,
            metrics=validated_metrics,
            narrative=narrative,
            audio_base64=audio_base64,
            warnings=warnings,
            persisted_id=persisted_id,
        )

    def _maybe_persist(
        self,
        route: AcademiaRoute,
        profile: ExerciseProfile,
        identification: ExerciseIdentification,
        practitioner: PractitionerHint,
        analysis_status: AnalysisStatus,
        metrics: BaseModel,
        narrative: str | None,
        warnings: list[str],
        audio_wav: bytes | None,
        persist: bool | None,
    ) -> int | None:
        # Persistência exige dois opt-ins independentes: capacidade administrativa
        # habilitada no servidor e pedido explícito desta requisição. Um cliente não
        # pode transformar ACADEMIA_PERSIST=false em gravação enviando persist=true.
        if persist is not True:
            return None
        if not self.cfg.persistence_available:
            warnings.append(
                "persistência desativada ou sem proteção administrativa; o relatório não foi salvo."
            )
            emit(
                catalog.ACADEMIA_WARNING,
                level="warning",
                data={"stage": "persist", "reason": "server_disabled"},
            )
            return None
        try:
            from . import store

            persisted_id = store.save(
                exercise=profile.slug,
                methodology_version=profile.methodology_version,
                practitioner_id=_clean_optional(practitioner.id),
                practitioner_name=_clean_optional(practitioner.name),
                capture_angle=route.info.capture_angle,
                analysis_status=analysis_status,
                result_json={
                    "identification": identification.model_dump(),
                    "route": route.info.model_dump(),
                    "metrics": metrics.model_dump(),
                    "narrative": narrative,
                    "warnings": list(warnings),
                },
                audio_wav=audio_wav,
            )
            if persisted_id is not None:
                emit(
                    catalog.ACADEMIA_PERSISTED,
                    data={
                        "id": persisted_id,
                        "exercise": profile.slug,
                        "analysis_status": analysis_status,
                        "has_audio": audio_wav is not None,
                    },
                )
            else:
                warnings.append(
                    "persistência indisponível; a análise foi concluída, mas o relatório não foi salvo."
                )
                emit(
                    catalog.ACADEMIA_WARNING,
                    level="warning",
                    data={"stage": "persist", "reason": "pool_unavailable"},
                )
            return persisted_id
        except Exception as exc:
            warnings.append(
                "persistência indisponível; a análise foi concluída, mas o relatório não foi salvo."
            )
            emit(
                catalog.ACADEMIA_WARNING,
                level="warning",
                error=exc,
                data={"stage": "persist"},
            )
            return None

    async def _save_upload(self, upload: UploadFile) -> tuple[str, int, str]:
        if upload is None or not (upload.filename or "").strip():
            raise EmptyUpload()
        descriptor, path = tempfile.mkstemp(
            prefix="bitvar_academia_",
            suffix=safe_video_suffix(upload.filename),
        )
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = await upload.read(self.cfg.upload_chunk_bytes)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.cfg.max_upload_bytes:
                        raise UploadTooLarge(self.cfg.max_upload_mb)
                    output.write(chunk)
            if size == 0:
                raise EmptyUpload()
            detected_mime = detect_video_mime(path)
            if detected_mime is None:
                raise InvalidVideo(
                    "formato de vídeo não reconhecido; envie MP4, MOV, WebM, AVI ou MPEG."
                )
            return path, size, detected_mime
        except BaseException:
            _safe_remove(path)
            raise

    async def _save_voice_upload(
        self,
        upload: UploadFile,
    ) -> tuple[str, int, str]:
        if upload is None:
            raise EmptyVoiceAudio()
        normalized_mime = normalize_audio_content_type(upload.content_type)
        if not supported_audio_upload(normalized_mime):
            raise InvalidVoiceAudio(
                "formato de gravação não aceito; use o microfone desta página."
            )
        descriptor, path = tempfile.mkstemp(
            prefix="bitvar_academia_voice_",
            suffix=safe_audio_suffix(upload.filename, normalized_mime),
        )
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = await upload.read(self.cfg.upload_chunk_bytes)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.cfg.voice_max_upload_bytes:
                        raise VoiceAudioTooLarge(
                            self.cfg.academia_voice_max_upload_mb
                        )
                    output.write(chunk)
            if size == 0:
                raise EmptyVoiceAudio()
            return path, size, normalized_mime
        except BaseException:
            _safe_remove_voice(path, phase="partial_source")
            raise


def _materialize_identification(
    raw: ExerciseIdentificationPass,
    *,
    duration_seconds: float | None,
) -> ExerciseIdentification:
    """Transforma o passe genérico em decisão pública e roteável.

    Somente ``exercise_family`` participa do roteamento. Os rótulos livres do
    modelo são higienizados e usados exclusivamente para apresentação.
    """

    family = raw.exercise_family
    candidate_profile = profile_for_family(family)
    methodology_scope = (
        getattr(candidate_profile, "methodology_scope", "exercise_specific")
        if candidate_profile is not None
        else "none"
    )
    if raw.target_status != "tracked":
        reason = "target_ambiguous"
    elif raw.status == "mixed":
        reason = "mixed"
    elif raw.status == "no_exercise":
        reason = "no_exercise"
    elif raw.status != "identified" or family == "unknown":
        reason = "unknown"
    elif raw.confidence == "baixa":
        reason = "low_confidence"
    elif candidate_profile is not None and methodology_scope == "general_execution":
        reason = "general_supported"
    elif candidate_profile is not None:
        reason = "supported"
    else:
        reason = "unsupported"

    canonical_label = EXERCISE_FAMILY_LABELS.get(family)
    detected_label = _safe_detection_label(raw.exercise_name_pt_br)
    exercise_label = (
        detected_label
        if raw.status == "identified" and family != "unknown"
        else canonical_label
    )
    exercise_label = exercise_label or canonical_label
    if not exercise_label:
        exercise_label = "Exercício não identificado"

    start_s = raw.active_start_s
    end_s = raw.active_end_s
    active_interval = None
    if duration_seconds is not None and math.isfinite(duration_seconds):
        end_s = min(end_s, duration_seconds)
    if (
        math.isfinite(start_s)
        and math.isfinite(end_s)
        and start_s >= 0
        and end_s > start_s
        and (duration_seconds is None or start_s < duration_seconds)
    ):
        active_interval = {
            "start_s": round(float(start_s), 3),
            "end_s": round(float(end_s), 3),
        }

    return ExerciseIdentification.model_validate(
        {
            "status": raw.status,
            "exercise_family": family,
            "exercise_label": exercise_label,
            "variation": _safe_detection_label(raw.variation_pt_br),
            "confidence": raw.confidence,
            "equipment": {
                "category": raw.equipment_category,
                "name": _safe_detection_label(raw.equipment_name_pt_br),
            },
            "target": {
                "status": raw.target_status,
                "multiple_people_visible": raw.multiple_people_visible,
            },
            "multiple_exercises_visible": raw.multiple_exercises_visible,
            "active_interval": active_interval,
            "profile_slug": (
                candidate_profile.slug if candidate_profile is not None else None
            ),
            "methodology_available": candidate_profile is not None,
            "methodology_scope": methodology_scope,
            "reason": reason,
        }
    )


def _emit_identification(identification: ExerciseIdentification) -> None:
    """Emite somente enums/flags locais; rótulos livres não entram em eventos."""

    data = {
        "status": identification.status,
        "exercise_family": identification.exercise_family,
        "confidence": identification.confidence,
        "target_status": identification.target.status,
        "multiple_people_visible": identification.target.multiple_people_visible,
        "multiple_exercises_visible": identification.multiple_exercises_visible,
        "methodology_available": identification.methodology_available,
        "methodology_scope": identification.methodology_scope,
        "reason": identification.reason,
    }
    if identification.reason in {
        "supported",
        "general_supported",
        "unsupported",
    }:
        emit(catalog.ACADEMIA_EXERCISE_IDENTIFIED, data=data)
    else:
        emit(
            catalog.ACADEMIA_EXERCISE_UNRESOLVED,
            level="warning",
            data=data,
        )


def _identification_only_response(
    identification: ExerciseIdentification,
    *,
    practitioner: PractitionerHint,
    started: float,
) -> AcademiaAnalysisResponse:
    """Resultado honesto quando não há rota técnica segura para executar."""

    if identification.reason == "unsupported":
        analysis_status: AnalysisStatus = "unsupported_exercise"
        family_label = EXERCISE_FAMILY_LABELS.get(
            identification.exercise_family,
            "exercício de musculação",
        )
        narrative = (
            f"O movimento foi classificado como {family_label.lower()}, "
            "mas esta versão ainda não possui uma metodologia técnica habilitada "
            "para avaliá-lo. Nenhum checklist, score ou correção de outro exercício "
            "foi aplicado."
        )
        warning = (
            "exercício identificado, mas ainda sem metodologia técnica BITVAR."
        )
    elif identification.reason == "target_ambiguous":
        analysis_status = "recapture_required"
        narrative = (
            "Não foi possível acompanhar a pessoa-alvo sem ambiguidade durante toda "
            "a série. Grave novamente mantendo essa pessoa visível e descreva a roupa "
            "ou a posição dela quando houver outras pessoas no vídeo. Nenhuma análise "
            "técnica foi produzida."
        )
        warning = "pessoa-alvo ambígua ou não encontrada; nova captura necessária."
    else:
        analysis_status = "exercise_unknown"
        narrative = (
            "Não foi possível identificar com segurança um único exercício e sua "
            "variação neste vídeo. Grave uma série contínua, mantenha o movimento e "
            "o equipamento visíveis e evite misturar exercícios no mesmo arquivo. "
            "Nenhuma análise técnica foi produzida."
        )
        warning = "identificação automática inconclusiva; nenhuma metodologia foi aplicada."

    emit(
        catalog.ACADEMIA_REPORT_GENERATED,
        data={
            "status": analysis_status,
            "kind": "identification_only",
            "chars": len(narrative),
        },
    )
    emit(
        catalog.ACADEMIA_ANALYZE_COMPLETED,
        duration_ms=round((time.monotonic() - started) * 1000, 1),
        data={
            "exercise_family": identification.exercise_family,
            "analysis_status": analysis_status,
            "has_narrative": True,
            "has_audio": False,
            "warnings": 1,
            "persisted": False,
        },
    )
    return AcademiaAnalysisResponse(
        analysis_status=analysis_status,
        identification=identification,
        route=None,
        practitioner=practitioner if practitioner.provided() else None,
        metrics=None,
        narrative=narrative,
        audio_base64=None,
        warnings=[warning],
        persisted_id=None,
    )


def _safe_detection_label(value: object, max_length: int = 80) -> str | None:
    """Limita rótulos livres do modelo sem lhes conceder autoridade."""

    text = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in str(value or "")
    )
    text = " ".join(text.split()).strip(" .,:;|-")
    return text[:max_length] or None


_ANGLE_LABELS = {
    "frontal": "frontal",
    "lateral": "lateral",
    "posterior": "posterior",
    "diagonal": "diagonal",
}

_SQUAT_COACHING_CUES = {
    "stance_and_foot_position": (
        "Defina a base antes da primeira repetição e mantenha a largura e a direção "
        "dos pés estáveis ao longo da série."
    ),
    "foot_contact": (
        "Distribua o apoio pelo pé e preserve calcanhar e antepé em contato durante "
        "a descida, a mudança de direção e a subida."
    ),
    "knee_tracking": (
        "Conduza os joelhos na mesma direção geral dos pés, mantendo a trajetória "
        "controlada sem forçar uma posição rígida."
    ),
    "squat_depth": (
        "Repita a amplitude mais profunda que permita conservar apoio, equilíbrio e "
        "controle, procurando chegar ao mesmo ponto em cada repetição."
    ),
    "trunk_control": (
        "Mantenha a inclinação do tronco contínua e controlada, evitando queda ou "
        "mudança abrupta de posição durante a descida e a subida."
    ),
    "hip_knee_coordination": (
        "Inicie e reverta o movimento com quadril e joelhos participando de forma "
        "coordenada, sem uma articulação assumir o ciclo de modo abrupto."
    ),
    "tempo_control": (
        "Controle a descida e desacelere antes de mudar de direção, evitando queda, "
        "rebote ou pressa para iniciar a subida."
    ),
    "bilateral_symmetry": (
        "Procure repetir ritmo e amplitude entre os lados sem deslocamento lateral "
        "progressivo; uma vista centralizada ajuda a conferir esse padrão."
    ),
}

_GENERAL_COACHING_CUES = {
    "range_pattern": (
        "Repita uma amplitude controlável e semelhante, preservando os apoios e a "
        "trajetória do início ao retorno."
    ),
    "tempo_pattern": (
        "Use um ritmo que permita controlar a ida, desacelerar na mudança de direção "
        "e concluir o retorno sem soltar o movimento."
    ),
    "trajectory_pattern": (
        "Mantenha um percurso repetível do corpo, implemento ou parte móvel da "
        "máquina, sem mudar a trajetória no meio da série."
    ),
    "stability_pattern": (
        "Organize pés, banco, encosto e pegadas antes de iniciar e preserve os "
        "contatos relevantes durante todo o ciclo."
    ),
    "alignment_pattern": (
        "Mantenha as articulações visíveis acompanhando o percurso identificado, "
        "sem elevar, girar ou deslocar segmentos de forma progressiva."
    ),
    "equipment_pattern": (
        "Confira banco, encosto, plataforma e pegadores antes da série e percorra o "
        "curso da máquina sem perder contato nem bater no fim."
    ),
    "repetition_consistency_pattern": (
        "Busque o mesmo ponto inicial, amplitude, trajetória e ritmo nas repetições "
        "seguintes, encerrando a série quando o padrão deixar de ser repetível."
    ),
    "transition_pattern": (
        "Desacelere antes de cada mudança de direção e reinicie o retorno sem rebote, "
        "impacto ou relaxamento abrupto."
    ),
}

_HORIZONTAL_PULL_COACHING_CUES = {
    "range_pattern": (
        "Repita uma amplitude em que tronco e apoios permaneçam estáveis, procurando "
        "alcançar o mesmo ponto final em cada puxada."
    ),
    "tempo_pattern": (
        "Padronize a velocidade da puxada, o tempo de contração no ponto final e o "
        "retorno entre as repetições, ajustando a pausa ao objetivo do treino."
    ),
    "trajectory_pattern": (
        "Mantenha a trajetória horizontal pretendida e um ângulo de cotovelo coerente "
        "com a variação, sem transformar o gesto no meio da série."
    ),
    "stability_pattern": (
        "Mantenha pés, pelve e tronco apoiados, evitando usar balanço do corpo para "
        "completar a puxada."
    ),
    "alignment_pattern": (
        "Mantenha o pescoço confortável e os ombros afastados das orelhas, evitando "
        "elevar a cintura escapular durante a puxada."
    ),
    "equipment_pattern": (
        "Ajuste banco, encosto e pegadores para iniciar com apoio estável e completar "
        "o percurso sem perder contato ou bater no fim do curso."
    ),
    "repetition_consistency_pattern": (
        "Repita o mesmo ponto final, o mesmo caminho dos cotovelos e um ritmo semelhante "
        "do início ao fim da série."
    ),
    "transition_pattern": (
        "Desacelere o retorno, mantenha tensão e mude de direção sem rebote ou soltura "
        "abrupta dos pegadores."
    ),
}


def _capture_suggestion(criterion) -> str:
    angles = [
        _ANGLE_LABELS.get(angle, str(angle))
        for angle in criterion.observable_angles
        if angle in _ANGLE_LABELS
    ]
    angle_text = " ou ".join(angles[:2]) if angles else "que mostre a região relevante"
    return (
        f"Para conferir {criterion.label.lower()}, grave também em vista {angle_text}, "
        "mantendo a região e o percurso relevantes visíveis durante toda a série."
    )


def _prefixed_coaching_cue(cue: str, verdict: str) -> str:
    if verdict == "adequado":
        return "Mantenha este padrão: " + cue
    if verdict == "ajuste_leve":
        return "Refinamento sugerido: " + cue
    return cue


def _squat_coaching_suggestion(criterion, verdict: str) -> str:
    if verdict in {"nao_avaliavel", "nao_aplicavel"}:
        return _capture_suggestion(criterion)
    cue = _SQUAT_COACHING_CUES.get(
        criterion.id,
        criterion.correction_guidance,
    )
    return _prefixed_coaching_cue(cue, verdict)


def _general_coaching_suggestion(
    criterion,
    verdict: str,
    exercise_family: str,
) -> str:
    if verdict == "nao_aplicavel":
        return "Nenhum equipamento externo exige ajuste neste critério."
    if verdict == "nao_avaliavel":
        return _capture_suggestion(criterion)
    family_cues = (
        _HORIZONTAL_PULL_COACHING_CUES
        if exercise_family == "horizontal_pull"
        else _GENERAL_COACHING_CUES
    )
    cue = family_cues.get(criterion.id, criterion.correction_guidance)
    return _prefixed_coaching_cue(cue, verdict)


_GENERAL_PATTERN_TEXT: dict[str, dict[str, tuple[str, str, str | None]]] = {
    "range_pattern": {
        "consistente_controlada": (
            "adequado",
            "A amplitude permaneceu semelhante entre as repetições e sob controle visual.",
            None,
        ),
        "reduzida_consistente": (
            "adequado",
            "A amplitude observada foi reduzida, porém repetível e controlada; o vídeo não determina se ela é ideal para o objetivo do treino.",
            None,
        ),
        "variavel": (
            "ajuste_leve",
            "A amplitude variou de forma repetida entre as repetições observáveis.",
            "Padronize uma amplitude que possa ser repetida sem perder apoio, trajetória ou controle.",
        ),
        "encurtada_abruptamente": (
            "a_corrigir",
            "O movimento foi interrompido de modo abrupto em parte das repetições.",
            "Use uma amplitude controlável e mantenha início, mudança de direção e retorno claramente definidos.",
        ),
    },
    "tempo_pattern": {
        "lento_controlado": (
            "adequado",
            "O ritmo foi lento e deliberado, sem perda visual clara de controle.",
            None,
        ),
        "moderado_controlado": (
            "adequado",
            "O ritmo foi moderado, controlado e repetível no trecho observado.",
            None,
        ),
        "rapido_controlado": (
            "adequado",
            "O movimento foi rápido, mas manteve trajetória e transições visualmente controladas.",
            None,
        ),
        "rapido_sem_controle": (
            "a_corrigir",
            "A velocidade observada veio acompanhada de perda de controle na trajetória ou nas transições.",
            "Reduza o ritmo até conseguir repetir o percurso sem impacto, impulso excessivo ou perda de posição.",
        ),
        "irregular": (
            "ajuste_leve",
            "O ritmo mudou repetidamente entre as repetições, reduzindo a padronização da série.",
            "Escolha um ritmo que possa ser mantido de forma semelhante em toda a série.",
        ),
    },
    "trajectory_pattern": {
        "consistente_controlada": (
            "adequado",
            "A trajetória principal permaneceu consistente e controlada no plano visível.",
            None,
        ),
        "desvio_repetido": (
            "ajuste_leve",
            "A trajetória apresentou um desvio repetido no plano visível da câmera.",
            "Repita o movimento priorizando um percurso estável e compatível com o equipamento ou a tarefa.",
        ),
        "mudanca_abrupta": (
            "a_corrigir",
            "A trajetória mudou de forma abrupta durante o ciclo observado.",
            "Desacelere a transição e mantenha o percurso contínuo do início ao retorno.",
        ),
    },
    "stability_pattern": {
        "estavel": (
            "adequado",
            "Apoios e segmentos visíveis permaneceram estáveis durante as repetições.",
            None,
        ),
        "oscilacao_leve": (
            "ajuste_leve",
            "Houve oscilação visível dos apoios ou do corpo durante parte da série.",
            "Organize os apoios antes de iniciar e mantenha o corpo estável sem movimentos acessórios desnecessários.",
        ),
        "perda_repetida": (
            "a_corrigir",
            "A estabilidade foi perdida repetidamente ao longo da execução.",
            "Reduza a complexidade ou o ritmo da execução até conseguir manter apoios e posição de forma repetível.",
        ),
    },
    "alignment_pattern": {
        "coerente_no_plano_visivel": (
            "adequado",
            "O alinhamento das regiões visíveis permaneceu coerente no plano da câmera.",
            None,
        ),
        "variacao_repetida": (
            "ajuste_leve",
            "Foi observada variação repetida de alinhamento no plano visível da câmera.",
            "Controle a trajetória das articulações visíveis sem forçar uma posição única ou uma amplitude desconfortável.",
        ),
    },
    "equipment_pattern": {
        "contato_e_ajuste_estaveis": (
            "adequado",
            "O contato com o equipamento e a posição observável permaneceram estáveis.",
            None,
        ),
        "perda_de_contato": (
            "a_corrigir",
            "Houve perda visível de contato com o apoio, banco, pegador ou plataforma.",
            "Ajuste a posição inicial para manter contato estável com os pontos de apoio durante todo o ciclo.",
        ),
        "ajuste_ou_posicao_instavel": (
            "ajuste_leve",
            "A posição em relação ao equipamento mudou de forma repetida durante a série.",
            "Reorganize banco, apoios e pegada antes de iniciar, sem inventar uma regulagem numérica a partir do vídeo.",
        ),
        "impacto_no_fim_do_curso": (
            "a_corrigir",
            "Foi observado impacto ou chegada abrupta ao fim do curso do equipamento.",
            "Controle a aproximação ao fim do percurso e o retorno, evitando batidas ou soltura abrupta.",
        ),
    },
    "repetition_consistency_pattern": {
        "repeticoes_padronizadas": (
            "adequado",
            "As repetições mantiveram um padrão visual semelhante de percurso e controle.",
            None,
        ),
        "variacao_progressiva": (
            "ajuste_leve",
            "A qualidade visual mudou progressivamente ao longo das repetições.",
            "Interrompa a série antes de perder a padronização e use somente repetições que mantenham o padrão pretendido.",
        ),
        "muito_irregular": (
            "a_corrigir",
            "As repetições foram muito diferentes entre si em ritmo, percurso ou controle.",
            "Recomece com um padrão simples e repetível antes de buscar mais velocidade ou amplitude.",
        ),
    },
    "transition_pattern": {
        "transicoes_controladas": (
            "adequado",
            "Início, mudança principal de direção e finalização ocorreram de forma controlada.",
            None,
        ),
        "uso_de_impulso_ou_rebote": (
            "a_corrigir",
            "A mudança de direção apresentou impulso ou rebote repetido.",
            "Desacelere antes da mudança de direção e reinicie o retorno sem usar um impacto para inverter o movimento.",
        ),
        "travamento_ou_impacto_abrupto": (
            "a_corrigir",
            "A transição ou finalização apresentou travamento ou impacto abrupto.",
            "Finalize cada fase com controle, evitando colisão, soltura ou bloqueio brusco.",
        ),
    },
}

_GENERAL_NOT_OBSERVABLE = (
    "nao_avaliavel",
    "O critério não pôde ser observado com segurança neste ângulo ou nesta captura.",
    None,
)
_GENERAL_NOT_APPLICABLE = (
    "nao_aplicavel",
    "O critério de interação com equipamento não se aplica a esta execução.",
    None,
)


def _materialize_general_analysis(
    capture_pass: GeneralExecutionCapturePass,
    checklist_pass: GeneralExecutionChecklistPass | None,
    *,
    identification: ExerciseIdentification,
    profile: ExerciseProfile,
    duration_seconds: float | None,
    timeline_offset_s: float,
    active_end_s: float | None,
    warnings: list[str],
) -> dict:
    """Converte enums remotos em relatório técnico geral controlado localmente."""

    capture = capture_pass.capture_quality.model_dump()
    issues: list[str] = []
    instructions: list[str] = []

    def add_issue(condition: bool, issue: str, instruction: str) -> None:
        if condition:
            issues.append(issue)
            instructions.append(instruction)

    add_issue(
        capture.get("exercise_visible") is not True,
        "O exercício não permaneceu visível durante o trecho analisado.",
        "grave o ciclo completo, incluindo posição inicial e retorno",
    )
    add_issue(
        capture.get("relevant_body_regions_visible") is not True,
        "As regiões corporais necessárias para acompanhar o movimento ficaram cortadas ou ocluídas.",
        "afaste ou reposicione a câmera para incluir as articulações e apoios usados no exercício",
    )
    add_issue(
        capture.get("target_person_trackable") is not True,
        "Não foi possível acompanhar a mesma pessoa-alvo durante toda a série.",
        "descreva a roupa ou posição da pessoa e mantenha-a visível durante toda a série",
    )
    add_issue(
        capture.get("stable_camera") is not True,
        "A câmera não permaneceu estável.",
        "apoie a câmera em uma posição fixa",
    )
    add_issue(
        capture.get("adequate_lighting") is not True,
        "A iluminação limitou a leitura do movimento.",
        "grave com iluminação uniforme e sem contraluz",
    )
    equipment_required = identification.equipment.category not in {
        "bodyweight",
        "unknown",
    }
    add_issue(
        equipment_required and capture.get("equipment_visible") is not True,
        "A interação com o equipamento não permaneceu visível.",
        "inclua no quadro os apoios, pegadores, plataforma e percurso da máquina",
    )
    if capture.get("detected_camera_angle") == "unknown":
        issues.append("O ângulo da câmera não pôde ser determinado.")
        instructions.append("use uma vista frontal, lateral ou diagonal bem definida")
    reported_complete = max(
        int(capture_pass.movement.complete_repetitions or 0),
        sum(bool(item.complete) for item in capture_pass.repetitions),
    )
    core_observable = (
        capture.get("exercise_visible") is True
        and capture.get("target_person_trackable") is True
        and capture_pass.movement.exercise_detected is True
        and reported_complete >= 1
    )
    if not core_observable:
        capture["status"] = "inadequate"
    elif capture.get("status") != "adequate" or issues:
        capture["status"] = "limited"
    capture["issues"] = list(dict.fromkeys(issues))[:8]
    capture["recapture_instructions"] = list(dict.fromkeys(instructions))[:6]

    segment_duration = None
    if active_end_s is not None and active_end_s > timeline_offset_s:
        segment_duration = active_end_s - timeline_offset_s
    elif duration_seconds is not None:
        segment_duration = max(0.0, duration_seconds - timeline_offset_s)

    repetitions: list[dict] = []
    for position, raw in enumerate(capture_pass.repetitions, start=1):
        values = (raw.start_s, raw.transition_s, raw.end_s)
        visible = [value if value >= 0 and math.isfinite(value) else None for value in values]
        present = [value for value in visible if value is not None]
        chronological = present == sorted(present)
        within_segment = (
            segment_duration is None
            or all(value <= segment_duration + 0.5 for value in present)
        )
        timing_valid = (
            len(present) == 3
            and chronological
            and within_segment
            and visible[0] < visible[2]
            and visible[0] <= visible[1] <= visible[2]
        )
        complete = bool(raw.complete)
        if raw.complete and not timing_valid:
            warnings.append(
                f"marcos inconsistentes na repetição geral {position}; "
                "os tempos foram removidos, mas o ciclo visual foi preservado."
            )
        global_values = [
            round(value + timeline_offset_s, 3) if value is not None else None
            for value in visible
        ]
        if not timing_valid:
            global_values = [None, None, None]
        repetition_duration = (
            round(global_values[2] - global_values[0], 3)
            if complete
            and timing_valid
            and global_values[0] is not None
            and global_values[2] is not None
            else None
        )
        repetitions.append(
            {
                "index": position,
                "complete": complete,
                "start_s": global_values[0],
                "transition_s": global_values[1],
                "end_s": global_values[2],
                "duration_seconds": repetition_duration,
                "confidence": raw.confidence if complete and timing_valid else "baixa",
                "observation": (
                    "Ciclo completo com início, transição e retorno observáveis."
                    if complete and timing_valid
                    else (
                        "Ciclo visualmente completo, com marcos temporais inconclusivos."
                        if complete
                        else "Ciclo parcial ou inconclusivo."
                    )
                ),
            }
        )

    complete_repetitions = [item for item in repetitions if item["complete"]]
    durations = [
        item["duration_seconds"]
        for item in complete_repetitions
        if item["duration_seconds"] is not None
    ]
    average_duration = round(sum(durations) / len(durations), 3) if durations else None
    duration_variation = None
    if average_duration and len(durations) >= 2:
        variance = sum(
            (duration - average_duration) ** 2 for duration in durations
        ) / len(durations)
        duration_variation = round(math.sqrt(variance) / average_duration, 3)

    checklist_data = (
        checklist_pass.model_dump()
        if checklist_pass is not None
        else {}
    )
    assessment_confidence = checklist_data.get("assessment_confidence", "baixa")
    if checklist_pass is None and capture.get("status") != "inadequate":
        warnings.append(
            "o segundo passe técnico geral ficou indisponível; "
            "os critérios foram marcados como não avaliáveis."
        )
    complete_ids = [item["index"] for item in complete_repetitions][:12]
    checklist: list[dict] = []
    for criterion in profile.criteria:
        pattern = checklist_data.get(criterion.id)
        if criterion.id == "equipment_pattern" and (
            identification.equipment.category == "bodyweight"
        ):
            verdict, observation, correction = _GENERAL_NOT_APPLICABLE
        elif pattern in {"nao_observavel", None}:
            verdict, observation, correction = _GENERAL_NOT_OBSERVABLE
        elif pattern == "nao_aplicavel":
            verdict, observation, correction = _GENERAL_NOT_APPLICABLE
        else:
            verdict, observation, correction = _GENERAL_PATTERN_TEXT.get(
                criterion.id,
                {},
            ).get(pattern, _GENERAL_NOT_OBSERVABLE)
        checklist.append(
            {
                "id": criterion.id,
                "label": criterion.label,
                "verdict": verdict,
                "score": None,
                "confidence": (
                    assessment_confidence
                    if verdict in {"adequado", "ajuste_leve", "a_corrigir"}
                    else "baixa"
                ),
                "observation": observation,
                "correction": correction,
                "coaching_suggestion": _general_coaching_suggestion(
                    criterion,
                    verdict,
                    identification.exercise_family,
                ),
                "muscle_context": None,
                "evidence_timestamps_s": [],
                "affected_repetitions": (
                    complete_ids
                    if verdict in {"ajuste_leve", "a_corrigir"}
                    else []
                ),
            }
        )

    applicable = [
        item for item in checklist if item["verdict"] != "nao_aplicavel"
    ]
    evaluated = [
        item
        for item in applicable
        if item["verdict"] in {"adequado", "ajuste_leve", "a_corrigir"}
    ]
    corrections = [item for item in evaluated if item["verdict"] == "a_corrigir"]
    refinements = [item for item in evaluated if item["verdict"] == "ajuste_leve"]
    coverage = round(len(evaluated) / max(1, len(applicable)), 3)
    if (
        capture.get("status") == "adequate"
        and capture.get("confidence") == "alta"
        and len(complete_repetitions) >= 2
        and coverage >= 0.75
    ):
        reliability_level = "alta"
    elif (
        capture.get("status") != "inadequate"
        and len(complete_repetitions) >= 1
        and coverage >= 0.5
    ):
        reliability_level = "media"
    else:
        reliability_level = "baixa"
    reliability_basis = [
        f"{len(complete_repetitions)} repetição(ões) completa(s) utilizável(is)",
        f"{len(evaluated)} de {len(applicable)} critérios aplicáveis avaliados",
        f"qualidade da captura: {capture.get('status', 'inadequate')}",
    ]
    if capture.get("detected_camera_angle") != "unknown":
        reliability_basis.append(
            f"ângulo observado: {capture['detected_camera_angle']}"
        )

    if not evaluated:
        classification = "nao_avaliavel"
    elif not corrections and not refinements:
        classification = "adequada_ao_padrao_observado"
    elif not corrections and len(refinements) <= 2:
        classification = "parcialmente_adequada"
    elif len(corrections) + len(refinements) <= 2:
        classification = "parcialmente_adequada"
    else:
        classification = "necessita_ajustes"

    tempo_style = checklist_data.get("tempo_pattern", "nao_avaliavel")
    if tempo_style not in {
        "lento_controlado",
        "moderado_controlado",
        "rapido_controlado",
        "rapido_sem_controle",
        "irregular",
    }:
        tempo_style = "nao_avaliavel"
    training_relevance = _general_training_relevance(tempo_style)
    training_relevance["observable_emphasis"] = list(
        dict.fromkeys(
            [
                *getattr(profile, "observable_emphasis", ()),
                *training_relevance["observable_emphasis"],
            ]
        )
    )[:5]
    positives = [
        f"{item['label']}: {item['observation']}"
        for item in evaluated
        if item["verdict"] == "adequado"
    ][:6]
    by_criterion_id = {item["id"]: item for item in checklist}
    requested_focus_id = checklist_data.get("primary_focus")
    requested_focus = by_criterion_id.get(requested_focus_id)
    requested_focus_valid = (
        requested_focus is not None
        and requested_focus.get("verdict")
        not in {"nao_avaliavel", "nao_aplicavel"}
    )
    focus_candidates: list[dict] = []
    if requested_focus_valid and (
        requested_focus.get("verdict") == "a_corrigir" or not corrections
    ):
        focus_candidates.append(requested_focus)
    focus_candidates.extend(corrections)
    if requested_focus_valid:
        focus_candidates.append(requested_focus)
    focus_candidates.extend(refinements)
    focus_candidates.extend(
        item
        for item in evaluated
        if item.get("verdict") == "adequado"
    )
    ordered_focus: list[dict] = []
    seen_focus_ids: set[str] = set()
    for item in focus_candidates:
        criterion_id = item.get("id")
        if criterion_id and criterion_id not in seen_focus_ids:
            seen_focus_ids.add(criterion_id)
            ordered_focus.append(item)
    primary_focus_item = ordered_focus[0] if ordered_focus else None
    primary_focus_id = (
        primary_focus_item.get("id") if primary_focus_item is not None else None
    )
    focus_suggestions = [
        item.get("coaching_suggestion") or item.get("correction")
        for item in ordered_focus
        if item.get("coaching_suggestion") or item.get("correction")
    ]
    limitations = list(capture["issues"])
    limitations.extend(
        f"{item['label']} não pôde ser avaliado."
        for item in applicable
        if item["verdict"] == "nao_avaliavel"
    )
    limitations.extend(
        [
            "A análise visual não mede carga, esforço, proximidade da falha, força, fadiga ou dor.",
            "Um único vídeo não demonstra eficácia, adaptação futura ou transferência de performance.",
            "O vídeo não mede ativação muscular ou recrutamento por eletromiografia.",
        ]
    )
    movement_pass = capture_pass.movement
    return {
        "analysis_mode": "general_execution",
        "exercise": identification.exercise_family,
        "exercise_label": identification.exercise_label,
        "variation": identification.variation,
        "equipment": identification.equipment.model_dump(),
        "methodology_version": profile.methodology_version,
        "methodology_status": profile.methodology_status,
        "capture_quality": capture,
        "movement": {
            "exercise_detected": movement_pass.exercise_detected,
            "detected_repetitions": len(repetitions),
            "complete_repetitions": len(complete_repetitions),
            "confidence": movement_pass.confidence,
            "tempo_style": tempo_style,
            "range_consistency": movement_pass.range_consistency,
            "tempo_consistency": movement_pass.tempo_consistency,
            "trajectory_consistency": movement_pass.trajectory_consistency,
            "average_repetition_seconds": average_duration,
            "repetition_duration_variation": duration_variation,
            "overall_observation": (
                f"Foram detectadas {len(repetitions)} repetições, com "
                f"{len(complete_repetitions)} ciclos completos utilizáveis. "
                "Ritmo, amplitude e trajetória foram descritos apenas no que "
                "permaneceu visível."
            ),
        },
        "repetitions": repetitions,
        "checklist": checklist,
        "primary_focus_criterion_id": primary_focus_id,
        "execution_summary": {
            "classification": classification,
            "reliability": {
                "level": reliability_level,
                "coverage": coverage,
                "evaluated_criteria": len(evaluated),
                "applicable_criteria": max(1, len(applicable)),
                "complete_repetitions": len(complete_repetitions),
                "basis": reliability_basis[:6],
            },
        },
        "training_relevance": training_relevance,
        "expected_muscle_roles": list(
            getattr(profile, "expected_muscle_roles", ())
        )[:8],
        "positive_points": positives,
        "priority_improvement": focus_suggestions[0] if focus_suggestions else None,
        "secondary_improvements": [
            item.get("coaching_suggestion") or item.get("correction")
            for item in [*corrections, *refinements]
            if item.get("id") != primary_focus_id
            and (item.get("coaching_suggestion") or item.get("correction"))
        ][:3],
        "limitations": list(dict.fromkeys(limitations))[:8],
        "muscle_activation_notice": (
            "O vídeo não mede ativação muscular, força, fadiga ou recrutamento por "
            "eletromiografia. Os grupos citados são papéis esperados para a família "
            "do exercício, não uma medição individual."
        ),
        "literature_references": [
            {"citation": reference.citation, "url": reference.url}
            for reference in profile.literature_references
        ],
        "weighted_execution_score": None,
    }


def _general_training_relevance(tempo_style: str) -> dict:
    interpretations = {
        "lento_controlado": (
            ["controle deliberado", "maior duração observável de cada repetição"],
            "O ritmo lento e controlado pode ser coerente com prática de controle e padronização, mas o vídeo não demonstra maior eficácia ou hipertrofia.",
        ),
        "moderado_controlado": (
            ["controle do percurso", "repetibilidade entre ciclos"],
            "O ritmo moderado e controlado é compatível com uma execução padronizada; a adaptação depende também de carga, esforço, volume e objetivo.",
        ),
        "rapido_controlado": (
            ["intenção visível de velocidade", "manutenção do controle em maior ritmo"],
            "A velocidade controlada pode ser relevante para potência quando carga, intenção e programação são apropriadas, mas a transferência de performance não é inferível pelo vídeo.",
        ),
        "rapido_sem_controle": (
            ["velocidade elevada", "perda visual de padronização"],
            "A perda de controle deve ser corrigida antes de interpretar o movimento como trabalho de potência ou performance.",
        ),
        "irregular": (
            ["variação de ritmo", "baixa padronização da série"],
            "A irregularidade reduz a comparabilidade das repetições e impede atribuir uma finalidade de treino somente pela imagem.",
        ),
        "nao_avaliavel": (
            [],
            "O ritmo não pôde ser classificado com segurança e o vídeo não permite inferir eficácia ou desempenho futuro.",
        ),
    }
    emphasis, interpretation = interpretations.get(
        tempo_style,
        interpretations["nao_avaliavel"],
    )
    return {
        "observed_style": tempo_style,
        "observable_emphasis": emphasis,
        "performance_interpretation": interpretation,
        "cannot_determine_without": [
            "carga externa",
            "esforço e proximidade da falha",
            "volume semanal",
            "intervalos e recuperação",
            "objetivo do treino",
            "histórico de treinamento",
            "dor ou condição de saúde",
        ],
    }


def _normalize_methodology(
    metrics: dict,
    profile: ExerciseProfile,
    warnings: list[str],
) -> None:
    """Força o checklist canônico e marca itens ausentes como não avaliáveis."""
    metrics["analysis_mode"] = "exercise"
    metrics["exercise"] = profile.slug
    metrics["methodology_version"] = profile.methodology_version
    metrics["methodology_status"] = profile.methodology_status
    metrics["muscle_activation_notice"] = (
        "O vídeo não mede ativação muscular, força, fadiga ou recrutamento por "
        "eletromiografia. As menções musculares descrevem apenas papéis esperados "
        "na literatura e não provam fraqueza, compensação ou hiperatividade."
    )
    metrics["literature_references"] = [
        {
            "citation": reference.citation,
            "url": reference.url,
        }
        for reference in profile.literature_references
    ]
    capture = metrics.setdefault("capture_quality", {})
    movement = metrics.setdefault("movement", {})
    if (
        capture.get("status") == "inadequate"
        and capture.get("exercise_visible") is True
        and _target_is_trackable(capture)
        and movement.get("exercise_detected") is True
        and int(movement.get("complete_repetitions") or 0) >= profile.min_complete_reps
    ):
        capture["status"] = "limited"
        warnings.append(
            "o gate visual foi normalizado de inadequado para limitado porque o "
            "movimento e a pessoa-alvo permaneceram reconhecíveis."
        )
    detected_angle = capture.get("detected_camera_angle")
    returned = metrics.get("checklist") or []
    by_id: dict[str, dict] = {}
    duplicates: list[str] = []
    unknown: list[str] = []
    for item in returned:
        criterion_id = str(item.get("id") or "")
        if criterion_id not in profile.criterion_ids:
            unknown.append(criterion_id or "<vazio>")
        elif criterion_id in by_id:
            duplicates.append(criterion_id)
        else:
            by_id[criterion_id] = item
    canonical: list[dict] = []
    missing: list[str] = []
    inconsistent: list[str] = []
    for criterion in profile.criteria:
        item = by_id.get(criterion.id)
        if item is None:
            missing.append(criterion.id)
            item = CriterionAssessment(
                id=criterion.id,
                label=criterion.label,
                verdict="nao_avaliavel",
                score=None,
                confidence="baixa",
                observation="O critério não foi devolvido com evidência observável.",
                correction=None,
                coaching_suggestion=_capture_suggestion(criterion),
            ).model_dump(exclude_none=True)
        else:
            item["id"] = criterion.id
            item["label"] = criterion.label
            if item.get("verdict") in {"nao_avaliavel", "nao_aplicavel"}:
                if item.get("verdict") == "nao_aplicavel":
                    inconsistent.append(criterion.id)
                    item["verdict"] = "nao_avaliavel"
                item["score"] = None
                item["correction"] = None
            else:
                score = item.get("score")
                contradiction = (
                    isinstance(score, (int, float))
                    and (
                        (item.get("verdict") == "adequado" and score < 7)
                        or (
                            item.get("verdict") == "ajuste_leve"
                            and (score < 5 or score >= 7)
                        )
                        or (item.get("verdict") == "a_corrigir" and score >= 7)
                    )
                )
                if contradiction:
                    inconsistent.append(criterion.id)
                    item.update({
                        "verdict": "nao_avaliavel",
                        "score": None,
                        "confidence": "baixa",
                        "observation": (
                            "Veredito e nota retornaram contraditórios; o critério "
                            "foi marcado como não avaliável."
                        ),
                        "correction": None,
                        "coaching_suggestion": _capture_suggestion(criterion),
                        "evidence_timestamps_s": [],
                        "affected_repetitions": [],
                    })
                elif item.get("verdict") == "adequado":
                    item["correction"] = None
                elif item.get("verdict") == "ajuste_leve":
                    item["correction"] = None
                elif item.get("verdict") == "a_corrigir":
                    # A recomendação acionável vem da metodologia versionada,
                    # não de texto livre do vídeo/modelo.
                    item["correction"] = criterion.correction_guidance
        item["muscle_context"] = criterion.muscle_context
        angle_approximate = False
        if (
            detected_angle in {"frontal", "lateral", "posterior", "diagonal"}
            and detected_angle not in criterion.observable_angles
            and item.get("verdict") not in {"nao_avaliavel", "nao_aplicavel"}
        ):
            angle_approximate = True
            item["confidence"] = _lower_confidence(item.get("confidence"))
        item["coaching_suggestion"] = _squat_coaching_suggestion(
            criterion,
            item.get("verdict"),
        )
        item["observation"] = _criterion_observation(
            item.get("verdict"),
            approximate=angle_approximate,
        )
        if angle_approximate:
            metrics.setdefault("_angle_approximation_criteria", []).append(
                criterion.label
            )
        canonical.append(item)
    metrics["checklist"] = canonical
    contract_issues: list[str] = []
    if missing:
        contract_issues.append("ausentes: " + ", ".join(missing))
    if duplicates:
        contract_issues.append("duplicados: " + ", ".join(sorted(set(duplicates))))
    if unknown:
        contract_issues.append("desconhecidos: " + ", ".join(sorted(set(unknown))))
    if inconsistent:
        contract_issues.append(
            "veredito/nota contraditórios: " + ", ".join(inconsistent)
        )
    metrics["_checklist_contract_valid"] = not contract_issues
    if contract_issues:
        limitations = metrics.setdefault("limitations", [])
        limitations.append(
            "O checklist retornado ficou incompleto ou inconsistente ("
            + "; ".join(contract_issues)
            + "). Itens ausentes foram marcados como não avaliáveis."
        )
        warnings.append(
            "checklist normalizado para os oito critérios canônicos: "
            + "; ".join(contract_issues)
            + "."
        )
    if metrics.get("_angle_approximation_criteria"):
        warnings.append(
            "critérios fora do ângulo preferencial foram mantidos como estimativas "
            "qualitativas com confiança reduzida."
        )


def _sanitize_repetitions(
    metrics: dict,
    duration_seconds: float | None,
    warnings: list[str],
) -> None:
    """Mantém segmentos cronológicos e timestamps compatíveis com o vídeo."""
    clean: list[dict] = []
    for position, item in enumerate(metrics.get("repetitions") or [], start=1):
        item = dict(item)
        item["index"] = position
        timestamps = [item.get("start_s"), item.get("bottom_s"), item.get("end_s")]
        present = [value for value in timestamps if isinstance(value, (int, float))]
        timing_missing = bool(item.get("complete")) and len(present) != 3
        out_of_bounds = duration_seconds is not None and any(
            value > duration_seconds + 0.5 for value in present
        )
        chronological = present == sorted(present)
        phases = [
            phase
            for phase in (item.get("phases") or [])
            if isinstance(phase, dict) and phase.get("observable") is not False
        ]
        phase_values = [
            phase.get("timestamp_s")
            for phase in phases
            if isinstance(phase, dict)
            and isinstance(phase.get("timestamp_s"), (int, float))
        ]
        phase_order = {"inicio": 0, "descida": 1, "fundo": 2, "subida": 3, "fim": 4}
        phase_names = [phase.get("phase") for phase in phases]
        phase_ranks = [phase_order.get(name, -1) for name in phase_names]
        phases_consistent = (
            phase_values == sorted(phase_values)
            and phase_ranks == sorted(phase_ranks)
            and len(phase_names) == len(set(phase_names))
        )
        if len(present) == 3:
            phases_consistent = phases_consistent and all(
                present[0] <= value <= present[-1] for value in phase_values
            )
        if out_of_bounds or not chronological or timing_missing or not phases_consistent:
            warnings.append(
                f"timestamps inconsistentes na repetição {position}; "
                "os tempos foram removidos, mas o ciclo visual foi preservado."
            )
            item["start_s"] = None
            item["bottom_s"] = None
            item["end_s"] = None
            item["phases"] = []
            item["confidence"] = "baixa"
        else:
            item["phases"] = phases
        clean.append(item)
    metrics["repetitions"] = clean
    movement = metrics.setdefault("movement", {})
    movement["detected_repetitions"] = len(clean)
    movement["complete_repetitions"] = sum(bool(item.get("complete")) for item in clean)
    valid_repetition_ids = {item["index"] for item in clean}
    evidence_changed = False
    for criterion in metrics.get("checklist") or []:
        raw_timestamps = criterion.get("evidence_timestamps_s") or []
        timestamps = sorted(
            {
                float(value)
                for value in raw_timestamps
                if isinstance(value, (int, float))
                and value >= 0
                and (duration_seconds is None or value <= duration_seconds + 0.5)
            }
        )[:8]
        raw_repetitions = criterion.get("affected_repetitions") or []
        repetitions = sorted(
            {
                int(value)
                for value in raw_repetitions
                if isinstance(value, int) and value in valid_repetition_ids
            }
        )[:12]
        if timestamps != raw_timestamps or repetitions != raw_repetitions:
            evidence_changed = True
        criterion["evidence_timestamps_s"] = timestamps
        criterion["affected_repetitions"] = repetitions
    if evidence_changed:
        warnings.append(
            "referências de evidência fora do vídeo ou das repetições detectadas foram removidas."
        )


def _lower_confidence(value: object) -> str:
    return {
        "alta": "media",
        "media": "baixa",
        "baixa": "baixa",
    }.get(str(value or "").lower(), "baixa")


def _criterion_observation(
    verdict: str | None,
    *,
    approximate: bool = False,
) -> str:
    """Texto publicável vem da taxonomia, nunca de prosa livre do VLM."""
    suffix = (
        " A leitura é aproximada neste ângulo e recebeu confiança reduzida."
        if approximate
        else ""
    )
    if verdict == "adequado":
        return (
            "O critério foi marcado como adequado nas repetições observáveis."
            + suffix
        )
    if verdict == "ajuste_leve":
        return (
            "O critério ficou funcional, com uma oportunidade visual de refinamento."
            + suffix
        )
    if verdict == "a_corrigir":
        return (
            "O critério apresentou um ponto visual a ajustar nas repetições observáveis."
            + suffix
        )
    return "O critério não pôde ser avaliado com segurança nesta captura."


def _canonicalize_generated_text(metrics: dict, profile: ExerciseProfile) -> None:
    """Remove prosa livre do VLM antes de tela, persistência, narrativa ou TTS.

    O modelo decide apenas campos estruturados (flags, vereditos, confiança e
    evidências). Todo texto publicável é reconstruído de uma taxonomia local.
    """
    capture = metrics.setdefault("capture_quality", {})
    issues: list[str] = []
    instructions: list[str] = []

    def add(condition: bool, issue: str, instruction: str) -> None:
        if condition:
            issues.append(issue)
            instructions.append(instruction)

    add(
        capture.get("exercise_visible") is not True,
        f"O exercício {profile.label.lower()} não permaneceu visível com segurança.",
        f"grave somente {profile.label.lower()}, do início ao fim de cada repetição",
    )
    add(
        capture.get("whole_body_visible") is not True,
        "O corpo inteiro não permaneceu no quadro.",
        "mantenha cabeça, tronco, quadril, joelhos e pés no quadro",
    )
    add(
        capture.get("feet_visible") is not True,
        "Os pés não permaneceram visíveis.",
        "afaste a câmera até incluir os pés durante toda a série",
    )
    add(
        not _target_is_trackable(capture),
        "Não foi possível acompanhar a pessoa-alvo durante todo o vídeo.",
        "descreva a roupa ou posição da pessoa-alvo e mantenha-a visível durante a série",
    )
    add(
        capture.get("stable_camera") is not True,
        "A estabilidade da câmera não pôde ser confirmada.",
        "apoie a câmera em posição fixa, aproximadamente na altura do quadril",
    )
    add(
        capture.get("adequate_lighting") is not True,
        "A iluminação não sustentou a leitura de todo o movimento.",
        "grave com iluminação uniforme e sem contraluz",
    )
    if capture.get("detected_camera_angle") == "unknown":
        issues.append("O ângulo da câmera não pôde ser determinado.")
        instructions.append("use uma vista lateral, diagonal ou frontal bem definida")
    if capture.get("status") in {"limited", "inadequate"} and not issues:
        issues.append("A captura foi classificada como limitada para esta análise.")
        instructions.append(
            "regrave de "
            f"{profile.recommended_min_reps} a {profile.recommended_max_reps} "
            "repetições seguindo o guia de captura"
        )
    capture["issues"] = list(dict.fromkeys(issues))[:8]
    capture["recapture_instructions"] = list(dict.fromkeys(instructions))[:6]

    movement = metrics.setdefault("movement", {})
    detected = int(movement.get("detected_repetitions") or 0)
    complete = int(movement.get("complete_repetitions") or 0)
    movement["range_consistency"] = _canonical_consistency(
        movement.get("range_consistency")
    )
    movement["tempo_consistency"] = _canonical_consistency(
        movement.get("tempo_consistency")
    )
    movement["overall_observation"] = (
        f"Foram detectadas {detected} repetições, das quais {complete} completas "
        "e utilizáveis para a leitura observacional."
    )
    for repetition in metrics.get("repetitions") or []:
        repetition["observation"] = (
            "Repetição completa com marcos temporais observáveis."
            if repetition.get("complete")
            else "Repetição parcial ou com marcos temporais inconclusivos."
        )

    limitations = list(capture["issues"])
    for item in metrics.get("checklist") or []:
        if item.get("verdict") == "nao_avaliavel":
            limitations.append(f"{item.get('label', 'Critério')} não pôde ser avaliado.")
    approximate_criteria = metrics.get("_angle_approximation_criteria") or []
    if approximate_criteria:
        limitations.append(
            "O ângulo não era o preferencial para "
            + ", ".join(approximate_criteria[:3])
            + "; esses itens foram mantidos como estimativas qualitativas com "
            "confiança reduzida."
        )
    if metrics.get("_checklist_contract_valid") is False:
        limitations.append("O checklist retornado precisou de normalização defensiva.")
    metrics["limitations"] = list(dict.fromkeys(limitations))[:8]


def _canonical_consistency(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"consistente", "estavel", "estável", "uniforme"}:
        return "consistente"
    if normalized in {"variavel", "variável", "inconsistente", "irregular"}:
        return "variável"
    return "inconclusivo"


def _derive_constructive_summary(metrics: dict) -> None:
    """Acertos/correções vêm do checklist, impedindo texto solto contraditório."""
    checklist = metrics.get("checklist") or []
    positives = [
        f"{item['label']}: {item['observation']}"
        for item in checklist
        if item.get("verdict") == "adequado" and item.get("observation")
    ]
    corrections = [item for item in checklist if item.get("verdict") == "a_corrigir"]
    refinements = [item for item in checklist if item.get("verdict") == "ajuste_leve"]
    metrics["positive_points"] = positives[:6]
    by_id = {item.get("id"): item for item in checklist if item.get("id")}
    requested_focus = by_id.get(metrics.get("primary_focus_criterion_id"))
    requested_focus_valid = (
        requested_focus is not None
        and requested_focus.get("verdict")
        not in {"nao_avaliavel", "nao_aplicavel"}
    )
    candidates: list[dict] = []
    if requested_focus_valid and (
        requested_focus.get("verdict") == "a_corrigir" or not corrections
    ):
        candidates.append(requested_focus)
    candidates.extend(corrections)
    if requested_focus_valid:
        candidates.append(requested_focus)
    candidates.extend(refinements)
    candidates.extend(
        item for item in checklist if item.get("verdict") == "adequado"
    )
    ordered: list[dict] = []
    seen: set[str] = set()
    for item in candidates:
        criterion_id = item.get("id")
        if criterion_id and criterion_id not in seen:
            seen.add(criterion_id)
            ordered.append(item)
    primary = ordered[0] if ordered else None
    metrics["primary_focus_criterion_id"] = (
        primary.get("id") if primary is not None else None
    )
    metrics["priority_improvement"] = (
        primary.get("coaching_suggestion") if primary is not None else None
    )
    metrics["secondary_improvements"] = [
        item.get("coaching_suggestion")
        for item in [*corrections, *refinements]
        if item is not primary and item.get("coaching_suggestion")
    ][:3]


def _decide_analysis_status(metrics: dict, profile: ExerciseProfile) -> AnalysisStatus:
    capture = metrics.get("capture_quality") or {}
    movement = metrics.get("movement") or {}
    critical_failure = (
        capture.get("status") == "inadequate"
        or capture.get("exercise_visible") is not True
        or not _target_is_trackable(capture)
        or movement.get("exercise_detected") is not True
        or movement.get("complete_repetitions", 0) < profile.min_complete_reps
    )
    if critical_failure:
        return "recapture_required"
    assessable = sum(
        item.get("verdict") != "nao_avaliavel" for item in metrics.get("checklist") or []
    )
    if (
        capture.get("status") == "limited"
        or capture.get("whole_body_visible") is not True
        or capture.get("feet_visible") is not True
        or metrics.get("_checklist_contract_valid") is False
        or capture.get("stable_camera") is not True
        or capture.get("adequate_lighting") is not True
        or capture.get("confidence") == "baixa"
        or capture.get("detected_camera_angle") == "unknown"
        or movement.get("confidence") == "baixa"
        or movement.get("complete_repetitions", 0) < profile.recommended_min_reps
        or assessable < profile.min_scored_criteria
    ):
        return "limited"
    return "complete"


def _decide_general_analysis_status(
    metrics: dict,
    profile: ExerciseProfile,
) -> AnalysisStatus:
    capture = metrics.get("capture_quality") or {}
    movement = metrics.get("movement") or {}
    reliability = (metrics.get("execution_summary") or {}).get("reliability") or {}
    critical_failure = (
        capture.get("status") == "inadequate"
        or capture.get("exercise_visible") is not True
        or not _target_is_trackable(capture)
        or movement.get("exercise_detected") is not True
        or movement.get("complete_repetitions", 0) < profile.min_complete_reps
    )
    if critical_failure:
        return "recapture_required"
    if (
        capture.get("status") == "limited"
        or capture.get("relevant_body_regions_visible") is not True
        or (
            (metrics.get("equipment") or {}).get("category")
            not in {"bodyweight", "unknown"}
            and capture.get("equipment_visible") is not True
        )
        or capture.get("stable_camera") is not True
        or capture.get("adequate_lighting") is not True
        or capture.get("confidence") == "baixa"
        or capture.get("detected_camera_angle") == "unknown"
        or movement.get("confidence") == "baixa"
        or movement.get("complete_repetitions", 0) < profile.recommended_min_reps
        or reliability.get("level") != "alta"
    ):
        return "limited"
    return "complete"


def _suppress_unreliable_assessment(metrics: dict) -> None:
    """Captura reprovada nunca carrega um laudo técnico residual do modelo."""
    for item in metrics.get("checklist") or []:
        item["verdict"] = "nao_avaliavel"
        item["score"] = None
        item["confidence"] = "baixa"
        item["observation"] = "Não avaliável com segurança nesta captura."
        item["correction"] = None
        item["coaching_suggestion"] = None
        item["evidence_timestamps_s"] = []
        item["affected_repetitions"] = []
    metrics["positive_points"] = []
    metrics["priority_improvement"] = None
    metrics["secondary_improvements"] = []


def _suppress_unreliable_general_assessment(metrics: dict) -> None:
    for item in metrics.get("checklist") or []:
        if item.get("verdict") == "nao_aplicavel":
            continue
        item["verdict"] = "nao_avaliavel"
        item["score"] = None
        item["confidence"] = "baixa"
        item["observation"] = "Não avaliável com segurança nesta captura."
        item["correction"] = None
        item["coaching_suggestion"] = None
        item["evidence_timestamps_s"] = []
        item["affected_repetitions"] = []
    applicable = sum(
        item.get("verdict") != "nao_aplicavel"
        for item in metrics.get("checklist") or []
    )
    metrics["positive_points"] = []
    metrics["priority_improvement"] = None
    metrics["secondary_improvements"] = []
    metrics.setdefault("movement", {})["tempo_style"] = "nao_avaliavel"
    metrics["execution_summary"] = {
        "classification": "nao_avaliavel",
        "reliability": {
            "level": "baixa",
            "coverage": 0.0,
            "evaluated_criteria": 0,
            "applicable_criteria": max(1, applicable),
            "complete_repetitions": metrics.get("movement", {}).get(
                "complete_repetitions",
                0,
            ),
            "basis": [
                "a captura não sustentou uma avaliação técnica responsável"
            ],
        },
    }
    metrics["training_relevance"] = _general_training_relevance("nao_avaliavel")


def _recapture_report(metrics: dict, profile: ExerciseProfile) -> str:
    capture = metrics.get("capture_quality") or {}
    instructions = capture.get("recapture_instructions") or []
    if not instructions:
        instructions = [
            "mantenha a pessoa-alvo e o corpo inteiro, inclusive os pés, no quadro",
            "apoie a câmera e grave de "
            f"{profile.recommended_min_reps} a {profile.recommended_max_reps} "
            "repetições com boa iluminação",
        ]
    guidance = "; ".join(str(item).rstrip(".") for item in instructions[:4])
    return (
        "Não há imagem suficiente para emitir um relatório confiável desta execução. "
        f"Grave novamente: {guidance}. "
        "Nenhuma conclusão postural foi produzida a partir desta captura."
    )


def _general_recapture_report(metrics: dict, profile: ExerciseProfile) -> str:
    capture = metrics.get("capture_quality") or {}
    instructions = capture.get("recapture_instructions") or [
        "mantenha a pessoa-alvo, as articulações relevantes e os apoios no quadro",
        "grave pelo menos "
        f"{profile.recommended_min_reps} repetições completas com câmera fixa",
    ]
    guidance = "; ".join(str(item).rstrip(".") for item in instructions[:4])
    return (
        "A captura não oferece evidência visual suficiente para classificar esta "
        f"execução com confiabilidade. Grave novamente: {guidance}. "
        "Nenhuma conclusão sobre correção, eficácia, musculatura ou performance "
        "foi produzida a partir deste vídeo."
    )


def _target_is_trackable(capture: dict) -> bool:
    """Aceita outras pessoas quando a pessoa-alvo continua inequívoca."""

    if capture.get("target_person_trackable") is not None:
        return capture.get("target_person_trackable") is True
    return capture.get("single_person_visible") is True


def _general_fallback_report(
    metrics: dict,
    *,
    practitioner_name: str | None = None,
    limited: bool = False,
) -> str:
    name = (practitioner_name or "").strip()
    opening = f"{name}, a" if name else "A"
    label = metrics.get("exercise_label") or "exercício identificado"
    summary = metrics.get("execution_summary") or {}
    classification = {
        "adequada_ao_padrao_observado": "adequada ao padrão visual observado",
        "parcialmente_adequada": "parcialmente adequada, com ajustes localizados",
        "necessita_ajustes": "necessita de ajustes para ganhar controle e padronização",
        "nao_avaliavel": "não avaliável nesta captura",
    }.get(summary.get("classification"), "não avaliável nesta captura")
    reliability = summary.get("reliability") or {}
    coverage = round(float(reliability.get("coverage") or 0) * 100)
    positives = metrics.get("positive_points") or []
    strengths = (
        " Os acertos sustentados pelo vídeo foram: "
        + "; ".join(positives[:4])
        + "."
        if positives
        else " Nenhum acerto técnico pôde ser confirmado com segurança."
    )
    priority = metrics.get("priority_improvement")
    correction = (
        f" O principal foco prático é: {priority}."
        if priority
        else " Não foi possível definir um foco prático no trecho observável."
    )
    movement = metrics.get("movement") or {}
    tempo = {
        "lento_controlado": "lento e controlado",
        "moderado_controlado": "moderado e controlado",
        "rapido_controlado": "rápido e controlado",
        "rapido_sem_controle": "rápido com perda de controle",
        "irregular": "irregular",
        "nao_avaliavel": "não avaliável",
    }.get(movement.get("tempo_style"), "não avaliável")
    relevance = (metrics.get("training_relevance") or {}).get(
        "performance_interpretation",
        "O vídeo isolado não permite prever eficácia ou performance futura.",
    )
    limitations = metrics.get("limitations") or []
    limit_text = (
        " A leitura ficou limitada ao que permaneceu visível."
        if limited
        else ""
    )
    if limitations:
        limit_text += " Limitações principais: " + "; ".join(limitations[:2]) + "."
    roles = metrics.get("expected_muscle_roles") or []
    muscle_text = (
        " Papéis musculares esperados para esta família de movimento: "
        + "; ".join(roles[:4])
        + ". Isso é contexto educacional, não medição de ativação."
        if roles
        else ""
    )
    return (
        f"{opening} análise de {str(label).lower()} classificou a execução como "
        f"{classification}. A confiabilidade foi {reliability.get('level', 'baixa')}, "
        f"com cobertura visual de {coverage}% dos critérios aplicáveis. "
        f"O ritmo observado foi {tempo}."
        + strengths
        + correction
        + " "
        + relevance
        + limit_text
        + muscle_text
        + " Este relatório é educacional e não substitui avaliação presencial."
    )


def _fallback_report(
    metrics: dict,
    *,
    practitioner_name: str | None = None,
    limited: bool = False,
    exercise_label: str = "Agachamento",
) -> str:
    """Relatório acessível mínimo quando a chamada narrativa fica indisponível."""
    name = (practitioner_name or "").strip()
    opening = f"{name}, a" if name else "A"
    positives = metrics.get("positive_points") or []
    if positives:
        strengths = " Os pontos sustentados pelo vídeo foram: " + "; ".join(positives[:4]) + "."
    else:
        strengths = " Nenhum ponto positivo pôde ser confirmado com segurança nesta captura."
    priority = metrics.get("priority_improvement")
    correction = (
        f" O principal foco prático é: {priority}."
        if priority
        else " Não foi possível definir um foco prático nesta captura."
    )
    limitations = metrics.get("limitations") or []
    limit_text = (
        " A leitura é limitada; considere apenas os critérios marcados como observáveis."
        if limited else ""
    )
    if limitations:
        limit_text += " Limitações registradas: " + "; ".join(limitations[:3]) + "."
    muscle_context = next(
        (
            item.get("muscle_context")
            for item in metrics.get("checklist") or []
            if item.get("verdict") in {"adequado", "ajuste_leve", "a_corrigir"}
            and item.get("muscle_context")
        ),
        None,
    )
    muscle_text = (
        f" Contexto muscular educacional: {muscle_context} "
        "O vídeo não mede ativação muscular."
        if muscle_context
        else ""
    )
    return (
        f"{opening} análise visual de {exercise_label.lower()} foi concluída com a metodologia "
        f"{metrics.get('methodology_version', 'POC')}."
        + strengths
        + correction
        + limit_text
        + muscle_text
        + " Este retorno é educacional, não é diagnóstico e não substitui um profissional habilitado."
    )


def _narrative_safety_issue(text: str) -> str | None:
    """Filtro conservador para promessas clínicas, medidas ou prescrição de carga."""
    lowered = " ".join((text or "").lower().split())
    forbidden = {
        "aumente a carga": "load_prescription",
        "adicione carga": "load_prescription",
        "prescrevo": "prescription",
        "diagnóstico de": "clinical_diagnosis",
        "você tem uma lesão": "clinical_diagnosis",
        "voce tem uma lesao": "clinical_diagnosis",
        "garante prevenir": "prevention_guarantee",
        "garantia de evitar": "prevention_guarantee",
    }
    for phrase, reason in forbidden.items():
        if phrase in lowered:
            return reason
    if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:kg|quilos?|graus?)\b", lowered):
        return "unsupported_measurement"
    if re.search(
        r"\b(?:diagn[oó]st|les[aã]o|patolog|tratamento|reabilita|risco de les)",
        lowered,
    ):
        return "clinical_language"
    if re.search(
        r"\b(?:fa[cç]a|realize|execute)\s+(?:\d+|uma|duas|tr[eê]s|quatro|cinco)\s+"
        r"(?:s[eé]ries?|repeti[cç][oõ]es?|reps?)\b",
        lowered,
    ):
        return "training_prescription"
    if re.search(
        r"\b(?:aumente|adicione|reduza|diminua|retire|use|escolha|mude)\b"
        r".{0,32}\b(?:carga|quilos?|libras?)\b",
        lowered,
    ):
        return "load_prescription"
    guarantee_scan = lowered
    for safe_pattern in (
        r"\b(?:n[aã]o|nao)\s+(?:garant\w*|previne|prevenir[aá]|evita(?:r[aá])?)\b",
        r"\b(?:n[aã]o|nao)\s+(?:pode|permite)\s+garant\w*\b",
        r"\b(?:n[aã]o|nao)\s+(?:[eé]|ha|h[aá]|oferece|existe)\s+"
        r"(?:uma\s+)?garantia\b",
        r"\bsem\s+(?:qualquer\s+)?garantia\b",
        r"\bnem\s+(?:garant\w*|previne|prevenir[aá]|evita(?:r[aá])?)\b",
    ):
        guarantee_scan = re.sub(safe_pattern, "", guarantee_scan)
    if re.search(
        r"\b(?:garant\w*|previne|prevenir[aá]|evita(?:r[aá])?)\b",
        guarantee_scan,
    ):
        return "unsupported_guarantee"
    if re.search(
        r"\b(?:vai|ir[aá]|promete|assegura)\b.{0,36}"
        r"\b(?:hipertrofia|ganho de for[cç]a|performance|desempenho)\b",
        lowered,
    ):
        return "unsupported_performance_prediction"
    if re.search(
        r"\b(?:ativou|ativa mais|maior ativa[cç][aã]o|recrutou mais|"
        r"mais recrutad[oa]|menos ativad[oa])\b",
        lowered,
    ):
        return "unsupported_activation_measurement"
    muscle_term = (
        r"(?:m[uú]scul\w*|gl[uú]te\w*|quadr[ií]ceps|adutor\w*|"
        r"posterior(?:es)?|panturrilh\w*|core)"
    )
    unsupported_state = (
        r"(?:fraqueza|frac[oa]s?|inibid\w*|hiperativ\w*|encurtad\w*|"
        r"n[aã]o\s+ativ\w*)"
    )
    if re.search(rf"\b{muscle_term}\b.{{0,48}}\b{unsupported_state}\b", lowered):
        return "unsupported_muscle_inference"
    if re.search(rf"\b{unsupported_state}\b.{{0,48}}\b{muscle_term}\b", lowered):
        return "unsupported_muscle_inference"
    return None


def _clean_optional(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _safe_narrative_name(value: str | None) -> str | None:
    """Aceita apenas rótulo parecido com nome antes de enviá-lo à narrativa/TTS."""
    cleaned = " ".join((value or "").strip().split())
    if not cleaned or len(cleaned) > 80 or len(cleaned.split()) > 4:
        return None
    if not all(character.isalpha() or character in " -'’" for character in cleaned):
        return None
    normalized = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode()
    tokens = set(re.findall(r"[a-z]+", normalized.lower()))
    forbidden = {
        "ignore", "instrucao", "instrucoes", "prompt", "sistema", "diga", "fale",
        "diagnostico", "lesao", "carga", "serie", "series", "execute", "faca",
    }
    return None if tokens & forbidden else cleaned


def _limit_voice_transcript(text: str, max_chars: int) -> tuple[str, bool]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned, False
    candidate = cleaned[:max_chars].rstrip()
    if " " in candidate:
        word_boundary = candidate.rsplit(" ", 1)[0].rstrip(" ,;:-")
        if len(word_boundary) >= max_chars // 2:
            candidate = word_boundary
    return candidate, True


def _voice_failure_reason(exc: Exception) -> str:
    return {
        EmptyVoiceAudio: "empty",
        VoiceAudioTooLarge: "too_large",
        VoiceAudioTooLong: "too_long",
        InvalidVoiceAudio: "invalid_format",
        VoiceTranscriptionUnavailable: "unavailable",
        NoSpeechDetected: "no_speech",
    }.get(type(exc), "unknown")


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _safe_remove_voice(path: str, *, phase: str) -> None:
    """Remove áudio temporário e torna uma falha visível sem registrar o caminho."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        emit(
            catalog.ACADEMIA_WARNING,
            level="warning",
            status="error",
            data={
                "stage": "voice_temp_delete",
                "phase": phase,
                "error_type": type(exc).__name__,
            },
        )


__all__ = [
    "AcademiaService",
    "AnalysisBusy",
    "EmptyVoiceAudio",
    "EmptyUpload",
    "InvalidVoiceAudio",
    "InvalidVideo",
    "NoSpeechDetected",
    "UploadTooLarge",
    "UnverifiableVideoDuration",
    "VoiceAudioTooLarge",
    "VoiceAudioTooLong",
    "VoiceTranscriptionBusy",
    "VoiceTranscriptionUnavailable",
    "VideoTooLong",
]
