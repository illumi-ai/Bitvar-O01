"""Wrapper das três chamadas ao Gemini para o pipeline de academia.

Espelha ``app/tennis/gemini.py`` linha a linha (mesmo SDK ``google-genai``
2.8.0, mesma reconciliação blueprint↔SDK real — ver ``CLAUDE.md``): a chamada
1 usa ``response_mime_type`` + ``response_schema`` (Pydantic), ancorada no
padrão-ouro biomecânico consolidado do exercício via ``analysis_system_prompt``
(sem tool de busca — ``google_search`` não é combinável com ``response_schema``
e o tennis também não a usa); a chamada 2 é texto livre (narrativa);
a chamada 3 é TTS com retry manual, igual ao tênis.

Os helpers puros de áudio/Files-API (``_pcm_to_wav``, ``_rate_from_mime``,
``_split_for_tts``, ``_sentences``, ``_extract_audio``, ``_state``) são
domínio-agnósticos — reimportados de ``app.tennis.gemini`` em vez de
duplicados (regra do orquestrador: reusar helpers do tennis por import
quando fizer sentido, sem tocar no módulo tennis).
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from app.events import catalog, emit
from app.tennis.gemini import (
    _extract_audio,
    _pcm_to_wav,
    _rate_from_mime,
    _split_for_tts,
    _state,
)

from .config import AcademiaSettings
from .config import academia_settings as default_cfg
from .prompts import build_narrative_prompt, build_tts_prompt


class GeminiError(RuntimeError):
    """Falha ao chamar o Gemini (configuração, upload, geração ou TTS)."""


class AcademiaGemini:
    """Cliente fino sobre ``google.genai`` para o pipeline de academia."""

    def __init__(self, settings: AcademiaSettings = default_cfg, client=None):
        self.cfg = settings
        self._client = client  # injetável em testes

    # ----- cliente preguiçoso -----
    @property
    def client(self):
        if self._client is None:
            if not self.cfg.gemini_api_key:
                raise GeminiError(
                    "GEMINI_API_KEY ausente — configure a chave para usar a análise."
                )
            from google import genai  # import tardio: app sobe sem a lib/chave

            self._client = genai.Client(api_key=self.cfg.gemini_api_key)
        return self._client

    # ----- Files API: upload + espera ACTIVE -----
    def upload_video(self, path: str, mime_type: str | None = None):
        from google.genai import types

        emit(catalog.GEMINI_UPLOAD_STARTED, data={"mime": mime_type})
        t0 = time.monotonic()
        try:
            cfg = types.UploadFileConfig(mime_type=mime_type) if mime_type else None
            file = self.client.files.upload(file=path, config=cfg)
        except Exception as e:  # pragma: no cover - rede
            emit(catalog.GEMINI_UPLOAD_FAILED, level="error", status="error", error=e)
            raise GeminiError(f"falha no upload do vídeo: {e}") from e

        deadline = time.monotonic() + self.cfg.academia_files_active_timeout_s
        while _state(file) == "PROCESSING":
            if time.monotonic() > deadline:
                emit(catalog.GEMINI_UPLOAD_FAILED, level="error", status="error", data={"reason": "timeout"})
                raise GeminiError("tempo esgotado processando o vídeo na Files API.")
            time.sleep(self.cfg.academia_files_poll_interval_s)
            file = self.client.files.get(name=file.name)
        if _state(file) == "FAILED":
            emit(catalog.GEMINI_UPLOAD_FAILED, level="error", status="error", data={"reason": "files_api_failed"})
            raise GeminiError("a Files API falhou ao processar o vídeo.")
        emit(catalog.GEMINI_UPLOAD_ACTIVE, duration_ms=round((time.monotonic() - t0) * 1000, 1),
             data={"file": getattr(file, "name", None)})
        return file

    def delete_file(self, file) -> None:
        try:
            self.client.files.delete(name=file.name)
            emit(catalog.GEMINI_FILE_DELETED, level="debug", data={"file": getattr(file, "name", None)})
        except Exception:
            pass  # melhor-esforço; arquivos expiram sozinhos

    # ----- chamada 1: vídeo → JSON estruturado (schema Pydantic, padrão do tennis) -----
    def analyze(self, file, *, schema_model: type[BaseModel], system_prompt: str,
                fps: int, media_resolution: str) -> BaseModel:
        from google.genai import types

        emit(catalog.GEMINI_ANALYZE_STARTED, data={
            "model": self.cfg.academia_analysis_model, "fps": fps,
            "media_resolution": media_resolution, "schema": schema_model.__name__})
        t0 = time.monotonic()
        contents = [
            types.Part(
                file_data=types.FileData(
                    file_uri=file.uri, mime_type=getattr(file, "mime_type", None) or "video/mp4"
                ),
                video_metadata=types.VideoMetadata(fps=fps),  # eixo temporal
            ),
            types.Part(text=system_prompt),  # instrução ao final
        ]
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=self.cfg.academia_analysis_thinking_level),
            media_resolution=media_resolution,  # eixo espacial, por chamada
            response_mime_type="application/json",
            response_schema=schema_model,
            # SEM temperature/top_p/top_k/candidate_count (não recomendados no 3.x)
        )
        try:
            resp = self.client.models.generate_content(
                model=self.cfg.academia_analysis_model, contents=contents, config=config
            )
        except Exception as e:  # pragma: no cover - rede
            emit(catalog.GEMINI_CALL_FAILED, level="error", status="error", error=e, data={"call": "analyze"})
            raise GeminiError(f"falha na análise (chamada 1): {e}") from e

        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, schema_model):
            result = parsed
        else:
            text = getattr(resp, "text", None)
            if not text:
                emit(catalog.GEMINI_CALL_FAILED, level="error", status="error",
                     data={"call": "analyze", "reason": "empty"})
                raise GeminiError("a análise retornou vazio (sem JSON).")
            try:
                result = schema_model.model_validate_json(text)
            except Exception as e:
                emit(catalog.GEMINI_CALL_FAILED, level="error", status="error", error=e,
                     data={"call": "analyze", "reason": "invalid_json"})
                raise GeminiError(f"JSON da análise inválido para o schema: {e}") from e
        emit(catalog.GEMINI_ANALYZE_COMPLETED, duration_ms=round((time.monotonic() - t0) * 1000, 1),
             data={"schema": schema_model.__name__})
        return result

    # ----- chamada 2: JSON → narrativa PT-BR -----
    def narrate(self, metrics: dict, *, student_name: str | None = None) -> str:
        from google.genai import types

        emit(catalog.GEMINI_NARRATE_STARTED, data={"student_name": student_name})
        t0 = time.monotonic()
        prompt = build_narrative_prompt(metrics, student_name=student_name)
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=self.cfg.academia_narrative_thinking_level),
        )
        try:
            resp = self.client.models.generate_content(
                model=self.cfg.academia_analysis_model, contents=prompt, config=config
            )
        except Exception as e:  # pragma: no cover - rede
            emit(catalog.GEMINI_CALL_FAILED, level="error", status="error", error=e, data={"call": "narrate"})
            raise GeminiError(f"falha na narrativa (chamada 2): {e}") from e
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            emit(catalog.GEMINI_CALL_FAILED, level="error", status="error",
                 data={"call": "narrate", "reason": "empty"})
            raise GeminiError("a narrativa retornou vazia.")
        emit(catalog.GEMINI_NARRATE_COMPLETED, duration_ms=round((time.monotonic() - t0) * 1000, 1),
             data={"chars": len(text)})
        return text

    # ----- chamada 3: narrativa → áudio WAV (com retry) -----
    def synthesize(self, narrative: str) -> bytes:
        chunks = _split_for_tts(narrative, self.cfg.academia_tts_chunk_chars)
        emit(catalog.GEMINI_TTS_STARTED, data={"chunks": len(chunks), "voice": self.cfg.academia_tts_voice})
        t0 = time.monotonic()
        pcm = bytearray()
        rate = self.cfg.academia_tts_sample_rate
        for chunk in chunks:
            chunk_pcm, chunk_rate = self._tts_chunk(chunk)
            pcm.extend(chunk_pcm)
            rate = chunk_rate or rate
        wav = _pcm_to_wav(bytes(pcm), rate, self.cfg.academia_tts_channels, self.cfg.academia_tts_sample_width)
        emit(catalog.GEMINI_TTS_COMPLETED, duration_ms=round((time.monotonic() - t0) * 1000, 1),
             data={"bytes": len(wav), "chunks": len(chunks)})
        return wav

    def _tts_chunk(self, text: str) -> tuple[bytes, int | None]:
        from google.genai import types

        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.cfg.academia_tts_voice)
                )
            ),
        )
        last_err: Exception | None = None
        for attempt in range(1, self.cfg.academia_tts_max_retries + 1):
            try:
                resp = self.client.models.generate_content(
                    model=self.cfg.academia_tts_model, contents=build_tts_prompt(text), config=config
                )
                pcm, mime = _extract_audio(resp)
                if pcm:
                    return pcm, _rate_from_mime(mime, self.cfg.academia_tts_sample_rate)
                last_err = GeminiError("TTS retornou sem áudio (texto no lugar).")
            except Exception as e:  # 500 ocasional do TTS preview
                last_err = e
            if attempt < self.cfg.academia_tts_max_retries:
                emit(catalog.GEMINI_TTS_RETRY, level="warning", error=last_err, data={"attempt": attempt})
                time.sleep(self.cfg.academia_tts_retry_backoff_s * attempt)
        emit(catalog.GEMINI_TTS_FAILED, level="error", status="error", error=last_err,
             data={"attempts": self.cfg.academia_tts_max_retries})
        raise GeminiError(f"TTS falhou após {self.cfg.academia_tts_max_retries} tentativas: {last_err}")
