"""Sinks de eventos: stdout (JSON estruturado) e DB (lote)."""

from __future__ import annotations

import json
import logging
import sys

from .catalog import Level
from .models import Event

_LEVEL_TO_LOGGING = {
    "debug": logging.DEBUG, "info": logging.INFO,
    "warning": logging.WARNING, "error": logging.ERROR,
}


class StdoutSink:
    """Loga cada evento como uma linha JSON. Logger próprio (não herda o
    formato do uvicorn) para a linha sair limpa e parseável."""

    def __init__(self, min_level: str = "info"):
        self.min_rank = Level.rank(min_level)
        self.log = logging.getLogger("bitvar.events")
        if not getattr(self.log, "_bitvar_configured", False):
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.log.addHandler(handler)
            self.log.setLevel(logging.DEBUG)
            self.log.propagate = False
            self.log._bitvar_configured = True  # type: ignore[attr-defined]

    def __call__(self, event: Event) -> None:
        if Level.rank(event.level) < self.min_rank:
            return
        line = json.dumps(event.to_log(), ensure_ascii=False, default=str)
        self.log.log(_LEVEL_TO_LOGGING.get(event.level, logging.INFO), line)


class DbSink:
    """Grava um lote de eventos na tabela ``events`` (best-effort)."""

    def write(self, batch: list[Event]) -> int:
        from . import store

        return store.insert_batch([e.to_row() for e in batch])
