"""Extração do frame do momento exato de cada erro técnico (ffmpeg → JPEG).

Chamado pelo ``service.py`` logo após a harmonização, enquanto o vídeo
temporário ainda existe no disco. Só roda quando HÁ erro com ``timestamp_s``
(execução limpa não gera frame — o print existe para mostrar ao aluno o
instante do problema, não para ilustrar acertos).

Mesma filosofia de degradação graciosa do resto do módulo: qualquer falha
(ffmpeg ausente, timestamp além do fim do vídeo, timeout, frame vazio) vira
no máximo um ``warning`` na resposta — nunca derruba a análise.

O seek usa ``-ss`` ANTES de ``-i``: no ffmpeg moderno isso é frame-accurate
(decodifica do keyframe anterior e descarta) e muito mais rápido que o seek de
saída em vídeos de até 180 s.
"""

from __future__ import annotations

import base64
import subprocess

from .config import AcademiaSettings
from .config import academia_settings as default_cfg
from .models import ErroTecnico, FrameErro


def extract_error_frames(
    video_path: str,
    erros: list[ErroTecnico],
    cfg: AcademiaSettings = default_cfg,
) -> tuple[list[FrameErro], list[str]]:
    """Um JPEG por erro com ``timestamp_s``, na ordem original de ``erros``.

    Respeita ``academia_frames_enabled`` e o teto ``academia_frames_max``
    (erros além do teto são pulados com um warning explícito — nada de corte
    silencioso). Retorna ``(frames, warnings)``.
    """
    if not cfg.academia_frames_enabled:
        return [], []
    com_timestamp = [(i, e) for i, e in enumerate(erros) if e.timestamp_s is not None]
    if not com_timestamp:
        return [], []

    warnings: list[str] = []
    selecionados = com_timestamp[: cfg.academia_frames_max]
    if len(selecionados) < len(com_timestamp):
        warnings.append(
            f"frames dos erros: limitado a {cfg.academia_frames_max} de "
            f"{len(com_timestamp)} erros com timestamp."
        )

    frames: list[FrameErro] = []
    for indice, erro in selecionados:
        try:
            jpeg = _grab_frame(video_path, erro.timestamp_s, cfg)
        except Exception as e:  # ffmpeg ausente, timeout, saída vazia…
            warnings.append(
                f"frame do erro em ~{erro.timestamp_s:.0f}s indisponível: {_short(e)}"
            )
            continue
        frames.append(FrameErro(
            erro_index=indice,
            categoria=erro.categoria,
            timestamp_s=erro.timestamp_s,
            image_base64=base64.b64encode(jpeg).decode("ascii"),
        ))
    return frames, warnings


def _grab_frame(video_path: str, timestamp_s: float, cfg: AcademiaSettings) -> bytes:
    """JPEG (bytes) do vídeo em ``timestamp_s``; levanta em qualquer falha."""
    # scale: largura ≤ max_width mantendo proporção; -2 arredonda a altura para
    # par (exigência de alguns encoders). JPEG no stdout (-f image2pipe).
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, timestamp_s):.3f}",
        "-i", video_path,
        "-frames:v", "1",
        "-vf", f"scale='min({cfg.academia_frame_max_width},iw)':-2",
        "-q:v", "3",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, timeout=cfg.academia_frame_timeout_s, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"ffmpeg falhou").decode("utf-8", "replace").strip()[:200])
    if not proc.stdout:
        # timestamp além do fim do vídeo produz saída vazia com returncode 0
        raise RuntimeError("nenhum frame nesse instante (timestamp além do fim do vídeo?)")
    return proc.stdout


def _short(e: Exception) -> str:
    txt = str(e).strip() or e.__class__.__name__
    return txt[:160]


__all__ = ["extract_error_frames"]
