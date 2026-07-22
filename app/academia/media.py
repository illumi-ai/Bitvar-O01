"""Helpers puros de upload e duração para vídeos da Academia."""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess


def safe_video_suffix(filename: str) -> str:
    """Extensão saneada; o nome original nunca vira caminho local."""
    extension = re.sub(r"[^a-z0-9.]", "", os.path.splitext(filename)[1].lower())
    allowed = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mpeg", ".mpg"}
    return extension if extension in allowed else ".mp4"


def detect_video_mime(path: str) -> str | None:
    """Reconhece contêineres comuns por assinatura, sem confiar no MIME enviado."""
    try:
        with open(path, "rb") as stream:
            head = stream.read(64)
    except OSError:
        return None
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        return "video/quicktime" if brand == b"qt  " else "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video/x-msvideo"
    if head.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3")):
        return "video/mpeg"
    return None


def probe_duration_seconds(path: str) -> float | None:
    """Duração via ffprobe quando disponível, ou box ``mvhd`` de MP4/MOV."""
    return _ffprobe_duration(path) or _mp4_duration(path)


def _ffprobe_duration(path: str) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        value = completed.stdout.strip()
        duration = float(value) if value and value != "N/A" else None
        return duration if duration and duration > 0 else None
    except Exception:
        return None


def _mp4_duration(path: str) -> float | None:
    try:
        with open(path, "rb") as stream:
            moov = _find_box(stream, b"moov")
            if moov is None:
                return None
            stream.seek(moov[0])
            mvhd = _find_box(stream, b"mvhd", moov[1])
            if mvhd is None:
                return None
            stream.seek(mvhd[0])
            version_raw = stream.read(1)
            if not version_raw:
                return None
            version = version_raw[0]
            stream.read(3)
            if version == 1:
                stream.read(16)
                timescale = struct.unpack(">I", stream.read(4))[0]
                duration = struct.unpack(">Q", stream.read(8))[0]
            else:
                stream.read(8)
                timescale = struct.unpack(">I", stream.read(4))[0]
                duration = struct.unpack(">I", stream.read(4))[0]
            value = duration / timescale if timescale else None
            return value if value and value > 0 else None
    except Exception:
        return None


def _find_box(stream, target: bytes, end: int | None = None) -> tuple[int, int] | None:
    while True:
        if end is not None and stream.tell() >= end:
            return None
        header = stream.read(8)
        if len(header) < 8:
            return None
        size = struct.unpack(">I", header[:4])[0]
        box_type = header[4:8]
        header_length = 8
        if size == 1:
            extended = stream.read(8)
            if len(extended) != 8:
                return None
            size = struct.unpack(">Q", extended)[0]
            header_length = 16
        payload_start = stream.tell()
        if size == 0:
            current = payload_start
            stream.seek(0, 2)
            payload_end = stream.tell()
            stream.seek(current)
        else:
            if size < header_length:
                return None
            payload_end = payload_start + size - header_length
        if box_type == target:
            return payload_start, payload_end
        stream.seek(payload_end)
