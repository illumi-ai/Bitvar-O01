"""Validação e normalização de gravações curtas para transcrição.

O navegador escolhe o contêiner do ``MediaRecorder`` (normalmente WebM/Opus,
Ogg/Opus ou MP4/AAC). O Gemini recebe somente WAV PCM mono a 16 kHz, formato
oficialmente suportado e previsível. Nenhum comando usa shell ou URL remota.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time

_MIME_SUFFIXES = {
    "audio/aac": ".aac",
    "audio/mp3": ".mp3",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
    # Alguns navegadores rotulam um MediaStream somente de áudio com o MIME do
    # contêiner. O ffprobe/ffmpeg ainda exige uma faixa de áudio válida.
    "video/mp4": ".m4a",
    "video/webm": ".webm",
    # Fallback de MediaRecorder antigo; o conteúdo continua sendo aferido.
    "application/octet-stream": ".audio",
}
_ALLOWED_SUFFIXES = {".aac", ".m4a", ".mp3", ".mp4", ".ogg", ".wav", ".webm"}
_VOICE_TEMP_PREFIX = "bitvar_academia_voice_"


class AudioNormalizationError(RuntimeError):
    """O contêiner não pôde ser decodificado em um WAV seguro."""


class AudioProcessingUnavailable(AudioNormalizationError):
    """Falha operacional/timeout das ferramentas locais, não do contêiner."""


def normalize_audio_content_type(value: str | None) -> str:
    """Remove parâmetros de codec e normaliza o MIME declarado."""
    return (value or "").split(";", 1)[0].strip().lower()


def supported_audio_upload(value: str | None) -> bool:
    return normalize_audio_content_type(value) in _MIME_SUFFIXES


def safe_audio_suffix(filename: str | None, content_type: str | None) -> str:
    """Escolhe uma extensão local curta sem incorporar o nome do usuário."""
    mime = normalize_audio_content_type(content_type)
    if mime in _MIME_SUFFIXES:
        return _MIME_SUFFIXES[mime]
    extension = re.sub(
        r"[^a-z0-9.]",
        "",
        os.path.splitext(filename or "")[1].lower(),
    )
    return extension if extension in _ALLOWED_SUFFIXES else ".audio"


def audio_tools_available() -> bool:
    """A imagem de produção instala ambos; health degrada se estiverem ausentes."""
    return shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None


def cleanup_stale_voice_tempfiles(
    *,
    directory: str | None = None,
    max_age_seconds: float = 6 * 60 * 60,
    now: float | None = None,
) -> tuple[int, int]:
    """Remove somente temporários de voz antigos deixados por queda do processo.

    Arquivos recentes são preservados para não interferir em requisições em
    andamento. O retorno contém ``(removidos, falhas)`` e nunca expõe caminhos.
    """
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds deve ser positivo.")
    temp_directory = directory or tempfile.gettempdir()
    cutoff = (time.time() if now is None else now) - max_age_seconds
    removed = 0
    failed = 0
    try:
        entries = os.scandir(temp_directory)
    except OSError:
        return 0, 1
    with entries:
        for entry in entries:
            if not entry.name.startswith(_VOICE_TEMP_PREFIX):
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                    continue
                os.remove(entry.path)
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                failed += 1
    return removed, failed


def probe_audio_duration_seconds(path: str) -> float | None:
    """Afere duração sem permitir que o contêiner acesse recursos de rede."""
    if shutil.which("ffprobe") is None:
        return None
    command = [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe,crypto,data",
        "-select_streams",
        "a:0",
        "-show_entries",
        "format=duration:stream=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    durations: list[float] = []
    for raw in completed.stdout.splitlines():
        try:
            value = float(raw.strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            durations.append(value)
    return max(durations) if durations else None


def normalize_audio_to_wav(
    source_path: str,
    destination_path: str,
    *,
    max_seconds: float,
    timeout_seconds: float,
) -> None:
    """Converte a primeira faixa de áudio para WAV PCM mono a 16 kHz.

    A duração declarada pode estar ausente em WebM do ``MediaRecorder``. ``-t``
    limita a decodificação mesmo nesse caso; o serviço confere depois a duração
    real pelos frames do WAV. A whitelist impede qualquer busca de rede.
    """
    if not audio_tools_available():
        raise AudioProcessingUnavailable(
            "ferramentas de processamento de áudio indisponíveis."
        )
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file,pipe,crypto,data",
        "-i",
        source_path,
        "-map",
        "0:a:0",
        "-map_metadata",
        "-1",
        "-vn",
        "-sn",
        "-dn",
        "-t",
        f"{max_seconds + 0.25:.3f}",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        "-y",
        destination_path,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioProcessingUnavailable(
            "não foi possível preparar a gravação para transcrição."
        ) from exc
    if completed.returncode != 0:
        raise AudioNormalizationError(
            "formato de áudio inválido ou sem faixa de voz decodificável."
        )
    try:
        size = os.path.getsize(destination_path)
    except OSError as exc:
        raise AudioNormalizationError(
            "o áudio normalizado não pôde ser lido."
        ) from exc
    if size <= 44:
        raise AudioNormalizationError("a gravação não contém áudio utilizável.")


__all__ = [
    "AudioNormalizationError",
    "AudioProcessingUnavailable",
    "audio_tools_available",
    "cleanup_stale_voice_tempfiles",
    "normalize_audio_content_type",
    "normalize_audio_to_wav",
    "probe_audio_duration_seconds",
    "safe_audio_suffix",
    "supported_audio_upload",
]
