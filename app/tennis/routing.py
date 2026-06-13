"""Roteamento: gênero × modo → schema, fps, media_resolution, pesos (blueprint §02).

O modo é detectado pela duração do vídeo (limiar ~75 s), com override manual.
Quando a duração é desconhecida, cai para uma heurística de tamanho de arquivo.
A duração é lida via ``ffprobe`` (se existir) ou por um parser mínimo de MP4/MOV
(box ``mvhd``) — sem dependências externas.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from dataclasses import dataclass

from pydantic import BaseModel

from .benchmarks import numbers_for
from .config import tennis_settings as cfg
from .models import ClipAnalysis, Gender, MatchAnalysis, Mode, RouteInfo
from .weights import WEIGHT_MODEL_BY_GENDER

_GENDER_ALIASES = {
    "male": "male", "m": "male", "masc": "male", "masculino": "male", "homem": "male",
    "female": "female", "f": "female", "fem": "female", "feminino": "female", "mulher": "female",
}


def normalize_gender(value: str | None) -> Gender:
    """Aceita male/female, m/f, masculino/feminino… Default: male."""
    if not value:
        return "male"
    g = _GENDER_ALIASES.get(value.strip().lower())
    if g is None:
        raise ValueError(f"gênero inválido: {value!r} (use 'male' ou 'female')")
    return g  # type: ignore[return-value]


def normalize_mode_override(value: str | None) -> Mode | None:
    if not value:
        return None
    v = value.strip().lower()
    if v in ("clip", "match"):
        return v  # type: ignore[return-value]
    if v in ("auto", ""):
        return None
    raise ValueError(f"override de modo inválido: {value!r} (use 'clip', 'match' ou 'auto')")


@dataclass
class Route:
    """Decisão de roteamento completa para um vídeo."""

    info: RouteInfo
    schema_model: type[BaseModel]
    system_prompt: str  # preenchido pelo serviço a partir de prompts.analysis_system_prompt
    weight_model: str | None
    benchmark_numbers: dict


def decide_mode(
    duration: float | None,
    override: Mode | None,
    file_size_bytes: int | None,
) -> tuple[Mode, str]:
    """Retorna (modo, como_foi_decidido)."""
    if override is not None:
        return override, f"override={override}"
    if duration is not None:
        mode: Mode = "clip" if duration < cfg.clip_max_seconds else "match"
        return mode, f"duration={duration:.1f}s (limiar {cfg.clip_max_seconds:.0f}s)"
    if file_size_bytes is not None:
        limit = cfg.filesize_clip_max_mb * 1024 * 1024
        mode = "clip" if file_size_bytes < limit else "match"
        return mode, f"heuristic_filesize={file_size_bytes / 1024 / 1024:.0f}MB"
    return "clip", "default_clip"


def build_route(
    gender_in: str | None,
    *,
    duration: float | None,
    override: str | None,
    file_size_bytes: int | None,
) -> Route:
    """Monta a decisão de roteamento a partir das entradas do usuário."""
    gender = normalize_gender(gender_in)
    mode, detection = decide_mode(
        duration, normalize_mode_override(override), file_size_bytes
    )

    if mode == "clip":
        fps, media_res, schema_model, weight_model = (
            cfg.clip_fps, cfg.clip_media_resolution, ClipAnalysis, None,
        )
    else:
        fps, media_res, schema_model, weight_model = (
            cfg.match_fps, cfg.match_media_resolution, MatchAnalysis,
            WEIGHT_MODEL_BY_GENDER[gender],
        )

    info = RouteInfo(
        gender=gender,
        mode=mode,
        fps=fps,
        media_resolution=media_res,
        thinking_level=cfg.analysis_thinking_level,
        schema_name=f"{gender}·{mode}",
        weight_model=weight_model,
        duration_seconds=duration,
        mode_detection=detection,
    )
    return Route(
        info=info,
        schema_model=schema_model,
        system_prompt="",
        weight_model=weight_model,
        benchmark_numbers=numbers_for(gender),
    )


# --------------------------------------------------------------------------- #
# detecção de duração                                                          #
# --------------------------------------------------------------------------- #
def probe_duration_seconds(path: str) -> float | None:
    """Duração em segundos via ffprobe ou parser MP4; None se indeterminável."""
    return _ffprobe_duration(path) or _mp4_duration(path)


def _ffprobe_duration(path: str) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        value = out.stdout.strip()
        return float(value) if value and value != "N/A" else None
    except Exception:
        return None


def _mp4_duration(path: str) -> float | None:
    """Lê timescale/duration do box ``mvhd`` dentro de ``moov`` (MP4/MOV)."""
    try:
        with open(path, "rb") as f:
            moov = _find_box(f, b"moov")
            if moov is None:
                return None
            start, end = moov
            f.seek(start)
            mvhd = _find_box(f, b"mvhd", end)
            if mvhd is None:
                return None
            f.seek(mvhd[0])
            version = f.read(1)[0]
            f.read(3)  # flags
            if version == 1:
                f.read(16)  # creation + modification (8+8)
                timescale = struct.unpack(">I", f.read(4))[0]
                duration = struct.unpack(">Q", f.read(8))[0]
            else:
                f.read(8)  # creation + modification (4+4)
                timescale = struct.unpack(">I", f.read(4))[0]
                duration = struct.unpack(">I", f.read(4))[0]
            return duration / timescale if timescale else None
    except Exception:
        return None


def _find_box(f, target: bytes, end: int | None = None) -> tuple[int, int] | None:
    """Procura um box top-level; retorna (início_payload, fim_payload) ou None."""
    while True:
        if end is not None and f.tell() >= end:
            return None
        header = f.read(8)
        if len(header) < 8:
            return None
        size = struct.unpack(">I", header[:4])[0]
        btype = header[4:8]
        header_len = 8
        if size == 1:  # largesize 64-bit
            size = struct.unpack(">Q", f.read(8))[0]
            header_len = 16
        box_start = f.tell()
        if size == 0:  # vai até o EOF
            cur = box_start
            f.seek(0, 2)
            payload_end = f.tell()
            f.seek(cur)
        else:
            payload_end = box_start + size - header_len
        if btype == target:
            return box_start, payload_end
        f.seek(payload_end)
