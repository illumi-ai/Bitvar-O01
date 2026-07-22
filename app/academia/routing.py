"""Seleção do perfil/metodologia da vertical Academia.

Ao contrário do tênis, academia não possui eixo clip/match nem roteamento por
gênero. O exercício seleciona um perfil versionado; o ângulo é uma dica de
captura e nunca autorização para avaliar um critério invisível.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel

from .config import AcademiaSettings
from .config import academia_settings as cfg
from .models import AcademiaRouteInfo, CaptureAngle
from .profiles import ExerciseProfile, get_profile


_CAPTURE_ANGLE_ALIASES: dict[str, CaptureAngle] = {
    "frontal": "frontal",
    "frente": "frontal",
    "front": "frontal",
    "lateral": "lateral",
    "lado": "lateral",
    "side": "lateral",
    "posterior": "posterior",
    "traseira": "posterior",
    "tras": "posterior",
    "rear": "posterior",
    "back": "posterior",
    "diagonal": "diagonal",
    "45": "diagonal",
    "45 graus": "diagonal",
    "tres quartos": "diagonal",
    "unknown": "unknown",
    "desconhecido": "unknown",
    "auto": "unknown",
}


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\s_-]+", " ", text.strip().lower())


def normalize_capture_angle(value: str | None) -> CaptureAngle:
    if not value:
        return "unknown"
    angle = _CAPTURE_ANGLE_ALIASES.get(_normalize_text(value))
    if angle is None:
        raise ValueError(
            f"ângulo de captura inválido: {value!r} "
            "(use frontal, lateral, posterior, diagonal ou unknown)"
        )
    return angle


@dataclass
class AcademiaRoute:
    """Decisão completa que o serviço entrega ao pipeline Gemini."""

    info: AcademiaRouteInfo
    profile: ExerciseProfile
    schema_model: type[BaseModel]
    system_prompt: str = ""

    @property
    def weight_model(self) -> str:
        return self.profile.weighting_model


def build_route(
    exercise_in: ExerciseProfile | str | None,
    duration: float | None = None,
    capture_angle: str | None = None,
    settings: AcademiaSettings | None = None,
) -> AcademiaRoute:
    """Monta a rota do MVP e valida apenas parâmetros de domínio.

    O teto de duração é exposto em ``route.info``; o serviço decide se o upload
    deve ser rejeitado para poder emitir o evento/status HTTP apropriado.
    """

    if duration is not None and duration < 0:
        raise ValueError("duração do vídeo não pode ser negativa")
    active_cfg = settings or cfg
    profile = (
        exercise_in
        if isinstance(exercise_in, ExerciseProfile)
        else get_profile(exercise_in)
    )
    angle = normalize_capture_angle(capture_angle)
    info = AcademiaRouteInfo(
        exercise=profile.slug,
        exercise_label=profile.label,
        methodology_version=profile.methodology_version,
        methodology_status=profile.methodology_status,
        methodology_scope=getattr(
            profile,
            "methodology_scope",
            "exercise_specific",
        ),
        capture_angle=angle,
        fps=active_cfg.academia_fps,
        media_resolution=active_cfg.academia_media_resolution,
        thinking_level=active_cfg.analysis_thinking_level,
        schema_name=f"{profile.slug}·{profile.methodology_version}",
        duration_seconds=duration,
        max_duration_seconds=active_cfg.academia_video_max_seconds,
    )
    return AcademiaRoute(
        info=info,
        profile=profile,
        schema_model=profile.schema_model,
    )


__all__ = ["AcademiaRoute", "build_route", "normalize_capture_angle"]
