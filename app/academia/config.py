"""Configuração do módulo de academia, lida do ambiente (.env).

Independente do ``app/settings.py`` (que exige ``DATABASE_URL``): aqui tudo tem
default sensato e ``GEMINI_API_KEY`` é **opcional** — a app sobe sem a chave e
os endpoints de academia respondem 503 com mensagem clara até a chave existir.

A chave do Gemini é **compartilhada** com ``app/tennis/config.py`` (mesma env
var ``GEMINI_API_KEY``, ``env_prefix=""``) — é a mesma credencial de API, não
faz sentido duplicar. Os demais parâmetros calibráveis deste módulo usam
prefixo ``academia_`` no NOME DO CAMPO (não no env_prefix, que fica vazio como
no tennis) para não colidir com os equivalentes de tênis quando ambos
convivem no mesmo ambiente (ex.: ``ACADEMIA_PERSIST`` vs ``TENNIS_PERSIST``).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AcademiaSettings(BaseSettings):
    """Parâmetros do pipeline de academia (análise técnica de exercício por vídeo)."""

    # ----- credenciais / modelos -----
    gemini_api_key: str | None = None  # compartilhada com o tênis (mesma env var, sem prefixo)
    # chamada 1 (vídeo→JSON) e 2 (JSON→texto). Decisão do usuário em 23/07/2026, com
    # A/B no clipe 637 (leg press com risco de lesão, gabarito INCORRETA):
    # gemini-3.6-flash errou 3/4 rodadas ("adequada, sem risco, 100/100"), mesmo com a
    # triagem de risco no prompt; o pro acertou 2/2 — e foi o modelo que gerou o
    # gabarito de calibragem. O flash segue disponível via env ACADEMIA_ANALYSIS_MODEL
    # (mais barato/rápido), mas perde detecção de risco de lesão.
    academia_analysis_model: str = "gemini-3.1-pro-preview"
    academia_tts_model: str = "gemini-3.1-flash-tts-preview"  # chamada 3 (texto→áudio)
    academia_tts_voice: str = "Vindemiatrix"  # voz PT-BR (Gentle), mesmo padrão do tênis

    # ----- raciocínio (thinking_level, não thinking_budget numérico) -----
    academia_analysis_thinking_level: str = "high"    # verificação técnica das 7 categorias de erro
    academia_narrative_thinking_level: str = "high"   # narrativa calibrada (erro primeiro quando houver)

    # ----- vídeo / roteamento -----
    academia_clip_max_seconds: float = 180.0  # duração MÁXIMA do clipe de exercício; acima disso rejeita
    academia_fps: int = 24                    # eixo temporal — captura fases do movimento (excêntrica/concêntrica)
    academia_media_resolution: str = "MEDIA_RESOLUTION_MEDIUM"  # equilíbrio custo × detalhe articular
    academia_filesize_clip_max_mb: float = 60.0  # heurística quando a duração é desconhecida

    # ----- upload -----
    academia_max_upload_mb: int = 600           # validado na app, rejeita antes de subir
    academia_upload_chunk_bytes: int = 1024 * 1024  # grava em disco em chunks (não em RAM)

    # ----- Files API (espera o vídeo ficar ACTIVE antes de analisar) -----
    academia_files_active_timeout_s: float = 3600.0
    academia_files_poll_interval_s: float = 2.0

    # ----- TTS (contexto ~32k, sem streaming, erro 500 ocasional) -----
    academia_tts_max_retries: int = 3
    academia_tts_retry_backoff_s: float = 2.0
    academia_tts_chunk_chars: int = 1800   # quebra narrativas longas e concatena
    academia_tts_sample_rate: int = 24000  # PCM → WAV 24kHz mono 16-bit
    academia_tts_channels: int = 1
    academia_tts_sample_width: int = 2

    # ----- persistência -----
    # default False (opt-in): diferente do tênis (opt-out), o módulo de academia
    # nasce com persistência desligada por padrão — só grava no Postgres se o
    # operador habilitar explicitamente via ACADEMIA_PERSIST=true. Falhas de
    # persistência viram warnings, nunca erros — com DB ausente, simplesmente
    # não salva (store.save retorna None) e o fluxo segue normalmente.
    academia_persist: bool = False

    model_config = SettingsConfigDict(
        env_prefix="", case_sensitive=False, extra="ignore"
    )

    @property
    def configured(self) -> bool:
        """True se há chave do Gemini para operar o pipeline."""
        return bool(self.gemini_api_key)

    @property
    def max_upload_bytes(self) -> int:
        return self.academia_max_upload_mb * 1024 * 1024


academia_settings = AcademiaSettings()
