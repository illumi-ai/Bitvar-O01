"""Configuração independente da vertical Academia.

As credenciais/modelos Gemini usam os mesmos nomes de ambiente do restante da
aplicação. Limites e comportamento próprios da vertical têm prefixo
``ACADEMIA_``, evitando que uma calibração do agachamento altere o tênis.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AcademiaSettings(BaseSettings):
    # Credencial e modelos compartilháveis.
    gemini_api_key: str | None = None
    analysis_model: str = Field(
        default="gemini-3.1-pro-preview",
        validation_alias=AliasChoices("ACADEMIA_ANALYSIS_MODEL", "ANALYSIS_MODEL"),
    )
    transcription_model: str = Field(
        default="gemini-3.5-flash",
        validation_alias=AliasChoices(
            "ACADEMIA_TRANSCRIPTION_MODEL",
            "TRANSCRIPTION_MODEL",
        ),
    )
    tts_model: str = Field(
        default="gemini-3.1-flash-tts-preview",
        validation_alias=AliasChoices("ACADEMIA_TTS_MODEL", "TTS_MODEL"),
    )
    tts_voice: str = Field(
        default="Vindemiatrix",
        validation_alias=AliasChoices("ACADEMIA_TTS_VOICE", "TTS_VOICE"),
    )
    analysis_thinking_level: str = Field(
        default="high",
        validation_alias=AliasChoices(
            "ACADEMIA_ANALYSIS_THINKING_LEVEL", "ANALYSIS_THINKING_LEVEL"
        ),
    )
    narrative_thinking_level: str = Field(
        default="high",
        validation_alias=AliasChoices(
            "ACADEMIA_NARRATIVE_THINKING_LEVEL", "NARRATIVE_THINKING_LEVEL"
        ),
    )

    # A identificação genérica usa uma amostragem econômica; somente depois do
    # roteamento o perfil técnico recebe 8 fps para distinguir fases rápidas.
    academia_identification_fps: int = Field(default=2, ge=1, le=4)
    academia_identification_media_resolution: str = "MEDIA_RESOLUTION_LOW"
    academia_fps: int = Field(default=8, ge=1, le=24)
    academia_media_resolution: str = "MEDIA_RESOLUTION_HIGH"
    academia_video_max_seconds: float = Field(default=180.0, gt=0, le=600)
    academia_max_concurrent_analyses: int = Field(default=2, ge=1, le=16)

    # Descrição por voz da pessoa-alvo. O clipe é temporário, normalizado para
    # WAV e enviado inline ao Gemini; nunca participa da persistência.
    academia_voice_max_seconds: float = Field(default=30.0, gt=0, le=60)
    academia_voice_max_upload_mb: int = Field(default=8, ge=1, le=20)
    academia_max_concurrent_transcriptions: int = Field(default=4, ge=1, le=32)
    academia_voice_transcode_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        le=120,
    )
    academia_voice_request_overhead_mb: int = Field(default=1, ge=1, le=8)

    # Upload em chunks; ``MAX_UPLOAD_MB`` permanece compatível com /tennis.
    max_upload_mb: int = Field(
        default=600,
        ge=1,
        le=2048,
        validation_alias=AliasChoices("ACADEMIA_MAX_UPLOAD_MB", "MAX_UPLOAD_MB"),
    )
    upload_chunk_bytes: int = Field(default=1024 * 1024, ge=64 * 1024)
    # Folga apenas para os campos/headers multipart. O arquivo continua sujeito
    # a ``max_upload_mb`` e é recontado durante a cópia em chunks.
    academia_request_body_overhead_mb: int = Field(default=40, ge=1, le=256)

    # Files API.
    files_active_timeout_s: float = 3600.0
    files_poll_interval_s: float = 2.0

    # TTS.
    tts_max_retries: int = 3
    tts_retry_backoff_s: float = 2.0
    tts_chunk_chars: int = 1800
    tts_sample_rate: int = 24000
    tts_channels: int = 1
    tts_sample_width: int = 2

    # Persistência best-effort; nunca armazena o vídeo bruto.
    academia_persist: bool = False
    academia_history_token: SecretStr | None = Field(
        default=None,
        description=(
            "Bearer token administrativo para histórico/exportação. A persistência "
            "só fica disponível quando este token e ACADEMIA_PERSIST estão configurados."
        ),
    )

    model_config = SettingsConfigDict(
        env_prefix="", case_sensitive=False, extra="ignore", populate_by_name=True
    )

    @property
    def configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_request_body_bytes(self) -> int:
        return (
            self.max_upload_mb + self.academia_request_body_overhead_mb
        ) * 1024 * 1024

    @property
    def voice_max_upload_bytes(self) -> int:
        return self.academia_voice_max_upload_mb * 1024 * 1024

    @property
    def voice_max_request_body_bytes(self) -> int:
        return (
            self.academia_voice_max_upload_mb
            + self.academia_voice_request_overhead_mb
        ) * 1024 * 1024

    @property
    def history_configured(self) -> bool:
        return bool(
            self.academia_history_token
            and self.academia_history_token.get_secret_value()
        )

    @property
    def persistence_available(self) -> bool:
        return self.academia_persist and self.history_configured


academia_settings = AcademiaSettings()
