"""Modelo interno de evento + modelos de E/S da API + scrubbing de segredos."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel

from .catalog import category_for

# chaves cujo VALOR nunca pode ir para log/DB (auditoria não guarda segredo)
_SECRET_HINTS = (
    "api_key", "apikey", "password", "passwd", "secret", "token",
    "authorization", "auth", "database_url", "cookie", "credential",
)
_MAX_STR = 2000  # trunca strings gigantes no payload

# redige segredos embutidos em TEXTO LIVRE (ex.: DSN do Postgres em mensagens de
# erro do psycopg, que carregam usuário:senha) — error/message não passam por scrub()
_URI_CRED = re.compile(r"(\b[a-zA-Z][\w+.\-]*://[^:/?#\s]+:)([^@/?#\s]+)(@)")
_KV_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|authorization|password|passwd|secret)\b(\s*[:=]\s*)(\S+)"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def scrub_text(text: str) -> str:
    """Redige credenciais em texto livre (DSN ``://user:senha@``, ``key=...``)."""
    if not text:
        return text
    t = _URI_CRED.sub(r"\1***\3", text)
    return _KV_SECRET.sub(r"\1\2***", t)


def scrub(value):
    """Redige segredos e trunca strings grandes, recursivamente. JSON-safe."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(h in str(k).lower() for h in _SECRET_HINTS):
                out[k] = "***"
            else:
                out[k] = scrub(v)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        t = scrub_text(value)
        return t[:_MAX_STR] + "…[truncado]" if len(t) > _MAX_STR else t
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return scrub_text(str(value))  # datas, objetos etc. viram string redigida


@dataclass
class Event:
    name: str
    category: str = ""
    level: str = "info"
    status: str | None = None
    correlation_id: str | None = None
    actor: str | None = None
    source: str | None = None
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    duration_ms: float | None = None
    message: str | None = None
    data: dict = field(default_factory=dict)
    error: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: datetime = field(default_factory=_now)

    def __post_init__(self):
        if not self.category:
            self.category = category_for(self.name)
        self.data = scrub(self.data or {})
        if self.error:  # error/message são texto livre → redige credenciais embutidas
            self.error = scrub_text(str(self.error))[: _MAX_STR * 2]
        if self.message:
            self.message = scrub_text(self.message)

    def to_row(self) -> tuple:
        """Tupla na ordem das colunas do INSERT (ver store.INSERT_COLS)."""
        return (
            self.event_id, self.ts, self.category, self.name, self.level, self.status,
            self.correlation_id, self.actor, self.source, self.method, self.path,
            self.status_code, self.duration_ms, self.message, self.data, self.error,
        )

    def to_log(self) -> dict:
        """Dict compacto (sem None) para a linha JSON do stdout."""
        d = {
            "ts": self.ts.isoformat(), "level": self.level, "category": self.category,
            "event": self.name, "status": self.status, "cid": self.correlation_id,
            "actor": self.actor, "source": self.source, "method": self.method,
            "path": self.path, "status_code": self.status_code,
            "duration_ms": self.duration_ms, "msg": self.message, "error": self.error,
            "data": self.data or None,
        }
        return {k: v for k, v in d.items() if v is not None}


# --------------------------------------------------------------------------- #
# modelos de saída da API                                                      #
# --------------------------------------------------------------------------- #
class EventOut(BaseModel):
    id: int
    event_id: str
    ts: datetime
    category: str
    name: str
    level: str
    status: str | None = None
    correlation_id: str | None = None
    actor: str | None = None
    source: str | None = None
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    duration_ms: float | None = None
    message: str | None = None
    data: dict = {}
    error: str | None = None


class EventListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[EventOut]


class BusStats(BaseModel):
    enabled: bool
    running: bool
    queued: int
    dropped: int
    to_db: bool
    to_stdout: bool


class EventStats(BaseModel):
    since: datetime | None = None
    total: int
    by_category: dict[str, int]
    by_level: dict[str, int]
    top_events: list[dict]
    errors: int
    http_requests: int
    http_error_rate: float
    avg_http_ms: float | None = None
    bus: BusStats


class CatalogItem(BaseModel):
    name: str
    category: str
    description: str
