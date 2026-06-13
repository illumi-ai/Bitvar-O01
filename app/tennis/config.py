"""Configuração do módulo de tênis, lida do ambiente (.env).

Independente do ``app/settings.py`` (que exige ``DATABASE_URL``): aqui tudo tem
default sensato e ``GEMINI_API_KEY`` é **opcional** — a app sobe sem a chave e
os endpoints de tênis respondem 503 com mensagem clara até a chave existir.
Os nomes de modelo e parâmetros seguem o blueprint, mas são sobrescrevíveis por
env para calibração (ex.: ``GEMINI_ANALYSIS_MODEL``, ``CLIP_FPS``).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class TennisSettings(BaseSettings):
    """Parâmetros do pipeline de tênis (blueprint §03, §07, §10)."""

    # ----- credenciais / modelos -----
    gemini_api_key: str | None = None
    analysis_model: str = "gemini-3.5-flash"          # chamadas 1 e 2 (vídeo→JSON, JSON→texto)
    tts_model: str = "gemini-3.1-flash-tts-preview"   # chamada 3 (texto→áudio)
    tts_voice: str = "Vindemiatrix"                   # voz PT-BR (Gentle), blueprint §07

    # ----- raciocínio (substitui thinking_budget numérico do 2.5) -----
    analysis_thinking_level: str = "high"             # análise técnica/estatística
    narrative_thinking_level: str = "medium"          # texto a partir de dados já basta

    # ----- roteamento clip × match (blueprint §02) -----
    clip_max_seconds: float = 75.0                    # < limiar → clip; ≥ → match
    clip_fps: int = 4                                 # eixo temporal: mecânica fina do golpe
    match_fps: int = 1                                # 1 fps cobre a partida sem estourar 1M
    clip_media_resolution: str = "MEDIA_RESOLUTION_HIGH"     # detalhe espacial no clipe
    match_media_resolution: str = "MEDIA_RESOLUTION_MEDIUM"  # baixa p/ caber a partida longa
    # quando a duração é desconhecida e não há override, decide por tamanho do arquivo:
    filesize_clip_max_mb: float = 60.0

    # ----- upload (blueprint §10) -----
    max_upload_mb: int = 600                          # validado na app, rejeita antes de subir
    upload_chunk_bytes: int = 1024 * 1024            # grava em disco em chunks (não em RAM)

    # ----- Files API (espera o vídeo ficar ACTIVE antes de analisar) -----
    files_active_timeout_s: float = 300.0
    files_poll_interval_s: float = 2.0

    # ----- TTS (blueprint §07: contexto 32k, sem streaming, erro 500 ocasional) -----
    tts_max_retries: int = 3
    tts_retry_backoff_s: float = 2.0
    tts_chunk_chars: int = 1800                       # quebra narrativas longas e concatena
    tts_sample_rate: int = 24000                      # PCM → WAV 24kHz mono 16-bit
    tts_channels: int = 1
    tts_sample_width: int = 2

    # ----- persistência -----
    # default True: o Caio pediu para "salvar e mandar de volta" (spec E1). Falhas
    # de persistência viram warnings, nunca erros — com DB ausente, simplesmente
    # não salva (store.save retorna None) e o fluxo segue normalmente.
    tennis_persist: bool = True                       # grava no Postgres se DB disponível

    model_config = SettingsConfigDict(
        env_prefix="", case_sensitive=False, extra="ignore"
    )

    @property
    def configured(self) -> bool:
        """True se há chave do Gemini para operar o pipeline."""
        return bool(self.gemini_api_key)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


tennis_settings = TennisSettings()
