"""Configuração do sistema de eventos (auditoria + observabilidade).

Tudo via env, com defaults seguros. Dois sinks independentes:
* **stdout** (sempre): linha JSON por evento — observabilidade / agregadores de log;
* **db** (best-effort): tabela ``events`` no Postgres — auditoria consultável.

O stdout funciona mesmo com o banco fora, então nenhum evento é perdido por
completo: o DB é o índice consultável, o stdout é o backup durável.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class EventSettings(BaseSettings):
    events_enabled: bool = True
    events_to_stdout: bool = True
    events_to_db: bool = True

    # nível mínimo p/ logar no stdout (debug<info<warning<error)
    events_stdout_min_level: str = "info"

    # fila/worker
    events_queue_max: int = 10000          # acima disso, eventos são descartados (contados)
    events_batch_max: int = 200            # itens por flush no DB
    events_flush_interval_s: float = 0.5   # intervalo do worker de flush

    # retenção (limpeza periódica); 0 = manter para sempre
    events_retention_days: int = 30
    events_cleanup_interval_s: float = 3600.0

    # captura HTTP
    events_capture_http: bool = True
    # prefixos de path ignorados no middleware HTTP (evita spam de healthcheck e
    # auto-referência da própria API de eventos)
    events_skip_paths: str = "/health,/ready,/events,/docs,/openapi.json,/redoc,/favicon.ico"

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")

    @property
    def skip_path_list(self) -> list[str]:
        return [p.strip() for p in self.events_skip_paths.split(",") if p.strip()]


event_settings = EventSettings()
