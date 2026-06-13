"""Wrapper das três chamadas ao Gemini (blueprint §03 e §07).

Reconciliado com o SDK real ``google-genai`` 2.8.0:

* o blueprint cita ``response_format.text.schema`` (sintaxe que este SDK não tem)
  → usamos ``response_mime_type`` + ``response_schema`` (modelo Pydantic);
* ``media_resolution`` aceita ``MEDIA_RESOLUTION_HIGH/MEDIUM`` (não há ``ultra_high``);
* ``thinking_level`` e ``VideoMetadata(fps=...)`` batem com o blueprint;
* vídeo via Files API exige aguardar o estado ``ACTIVE`` antes de analisar.
"""

from __future__ import annotations

import io
import time
import wave

from pydantic import BaseModel

from app.events import catalog, emit

from .config import TennisSettings
from .config import tennis_settings as default_cfg
from .prompts import build_narrative_prompt, build_tts_prompt


class GeminiError(RuntimeError):
    """Falha ao chamar o Gemini (configuração, upload, geração ou TTS)."""


class TennisGemini:
    """Cliente fino sobre ``google.genai`` para o pipeline de tênis."""

    def __init__(self, settings: TennisSettings = default_cfg, client=None):
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

        deadline = time.monotonic() + self.cfg.files_active_timeout_s
        while _state(file) == "PROCESSING":
            if time.monotonic() > deadline:
                emit(catalog.GEMINI_UPLOAD_FAILED, level="error", status="error", data={"reason": "timeout"})
                raise GeminiError("tempo esgotado processando o vídeo na Files API.")
            time.sleep(self.cfg.files_poll_interval_s)
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

    # ----- chamada 1: vídeo → JSON estruturado -----
    def analyze(self, file, *, schema_model: type[BaseModel], system_prompt: str,
                fps: int, media_resolution: str) -> BaseModel:
        from google.genai import types

        emit(catalog.GEMINI_ANALYZE_STARTED, data={
            "model": self.cfg.analysis_model, "fps": fps,
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
            thinking_config=types.ThinkingConfig(thinking_level=self.cfg.analysis_thinking_level),
            media_resolution=media_resolution,  # eixo espacial, por chamada
            response_mime_type="application/json",
            response_schema=schema_model,
            # SEM temperature/top_p/top_k/candidate_count (não recomendados no 3.x)
        )
        try:
            resp = self.client.models.generate_content(
                model=self.cfg.analysis_model, contents=contents, config=config
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
    def narrate(self, metrics: dict, *, gender: str, mode: str, player_name: str | None = None) -> str:
        from google.genai import types

        emit(catalog.GEMINI_NARRATE_STARTED, data={"gender": gender, "mode": mode})
        t0 = time.monotonic()
        prompt = build_narrative_prompt(metrics, gender, mode, player_name=player_name)
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=self.cfg.narrative_thinking_level),
        )
        try:
            resp = self.client.models.generate_content(
                model=self.cfg.analysis_model, contents=prompt, config=config
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
        chunks = _split_for_tts(narrative, self.cfg.tts_chunk_chars)
        emit(catalog.GEMINI_TTS_STARTED, data={"chunks": len(chunks), "voice": self.cfg.tts_voice})
        t0 = time.monotonic()
        pcm = bytearray()
        rate = self.cfg.tts_sample_rate
        for chunk in chunks:
            chunk_pcm, chunk_rate = self._tts_chunk(chunk)
            pcm.extend(chunk_pcm)
            rate = chunk_rate or rate
        wav = _pcm_to_wav(bytes(pcm), rate, self.cfg.tts_channels, self.cfg.tts_sample_width)
        emit(catalog.GEMINI_TTS_COMPLETED, duration_ms=round((time.monotonic() - t0) * 1000, 1),
             data={"bytes": len(wav), "chunks": len(chunks)})
        return wav

    def _tts_chunk(self, text: str) -> tuple[bytes, int | None]:
        from google.genai import types

        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.cfg.tts_voice)
                )
            ),
        )
        last_err: Exception | None = None
        for attempt in range(1, self.cfg.tts_max_retries + 1):
            try:
                resp = self.client.models.generate_content(
                    model=self.cfg.tts_model, contents=build_tts_prompt(text), config=config
                )
                pcm, mime = _extract_audio(resp)
                if pcm:
                    return pcm, _rate_from_mime(mime, self.cfg.tts_sample_rate)
                last_err = GeminiError("TTS retornou sem áudio (texto no lugar).")
            except Exception as e:  # 500 ocasional do TTS preview
                last_err = e
            if attempt < self.cfg.tts_max_retries:
                emit(catalog.GEMINI_TTS_RETRY, level="warning", error=last_err, data={"attempt": attempt})
                time.sleep(self.cfg.tts_retry_backoff_s * attempt)
        emit(catalog.GEMINI_TTS_FAILED, level="error", status="error", error=last_err,
             data={"attempts": self.cfg.tts_max_retries})
        raise GeminiError(f"TTS falhou após {self.cfg.tts_max_retries} tentativas: {last_err}")


# --------------------------------------------------------------------------- #
# helpers puros (testáveis sem rede)                                           #
# --------------------------------------------------------------------------- #
def _state(file) -> str:
    state = getattr(file, "state", None)
    return getattr(state, "name", str(state)) if state is not None else "ACTIVE"


def _extract_audio(resp) -> tuple[bytes | None, str | None]:
    """Extrai PCM e mime_type da resposta de TTS, tolerando formatos."""
    candidates = getattr(resp, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data, getattr(inline, "mime_type", None)
    return None, None


def _rate_from_mime(mime: str | None, default: int) -> int:
    """Lê o sample rate de 'audio/L16;rate=24000'."""
    if not mime:
        return default
    for token in mime.replace(" ", "").split(";"):
        if token.startswith("rate="):
            try:
                return int(token.split("=", 1)[1])
            except ValueError:
                return default
    return default


def _pcm_to_wav(pcm: bytes, rate: int, channels: int, width: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _split_for_tts(text: str, max_chars: int) -> list[str]:
    """Quebra a narrativa em pedaços <= max_chars, respeitando fim de frase."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for sentence in _sentences(text):
        # frase isolada maior que o limite: fecha o atual e fatia no braço,
        # garantindo que nenhum pedaço emitido ultrapasse max_chars
        if len(sentence) > max_chars:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            for i in range(0, len(sentence), max_chars):
                chunks.append(sentence[i:i + max_chars])
            continue
        if current and len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text[:max_chars]]


def _sentences(text: str) -> list[str]:
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in ".!?\n" :
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if s]
