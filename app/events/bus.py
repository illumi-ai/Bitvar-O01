"""Event bus: emit() não-bloqueante + worker async que grava no DB em lote.

Design:
* ``emit()`` é seguro para chamar de qualquer thread (inclusive o threadpool do
  pipeline) e **nunca levanta exceção** — auditoria jamais derruba o request.
* O sink stdout roda síncrono dentro do emit (ordem + durabilidade imediata).
* O sink DB é alimentado por uma ``queue.Queue`` thread-safe e drenado por um
  worker asyncio em lotes; a escrita roda em threadpool (psycopg é síncrono).
* Fila cheia → descarta e conta (o dado ainda foi para o stdout).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time

from . import catalog
from .config import EventSettings
from .config import event_settings as default_cfg
from .models import Event
from .sinks import DbSink, StdoutSink

_log = logging.getLogger("bitvar.events.bus")


class EventBus:
    def __init__(self, settings: EventSettings = default_cfg):
        self.cfg = settings
        self._q: queue.Queue[Event] = queue.Queue(maxsize=settings.events_queue_max)
        self._stdout = StdoutSink(settings.events_stdout_min_level) if settings.events_to_stdout else None
        self._db = DbSink() if settings.events_to_db else None
        self._task: asyncio.Task | None = None
        self._running = False
        self._dropped = 0
        self._dropped_reported = 0
        self._lock = threading.Lock()  # protege _dropped (emit roda em threads)
        self._last_cleanup = 0.0

    # ----- emissão (hot path; nunca levanta) -----
    def emit(self, event: Event) -> None:
        if not self.cfg.events_enabled:
            return
        try:
            if self._stdout is not None:
                self._stdout(event)
            if self._db is not None:
                try:
                    self._q.put_nowait(event)
                except queue.Full:
                    with self._lock:
                        self._dropped += 1
        except Exception:  # pragma: no cover - blindagem total
            pass

    # ----- worker -----
    async def start(self) -> None:
        if self._db is None or self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="bitvar-events-worker")
        self.emit(Event(catalog.BUS_STARTED, level="debug",
                        data={"queue_max": self.cfg.events_queue_max}))

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(self.cfg.events_flush_interval_s)
            await self._flush()
            self._report_dropped()
            await self._maybe_cleanup()
        # flush final: drena TUDO (não só um lote), com guarda anti-loop
        guard = self.cfg.events_queue_max // max(1, self.cfg.events_batch_max) + 2
        while not self._q.empty() and guard > 0:
            await self._flush()
            guard -= 1
        self._report_dropped()

    def _report_dropped(self) -> None:
        """Emite EVENTS_DROPPED com o delta desde o último relato (histórico no DB)."""
        with self._lock:
            delta = self._dropped - self._dropped_reported
            self._dropped_reported = self._dropped
        if delta > 0:
            self.emit(Event(catalog.EVENTS_DROPPED, level="warning",
                            data={"dropped": delta, "total": self._dropped}))

    async def _flush(self) -> None:
        batch: list[Event] = []
        while len(batch) < self.cfg.events_batch_max:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return
        try:
            await asyncio.to_thread(self._db.write, batch)  # type: ignore[union-attr]
        except Exception as e:
            # DB indisponível: os eventos já foram para o stdout (backup durável).
            _log.warning("flush de eventos falhou (%d perdidos do DB): %s", len(batch), e)
            # registra a própria falha (só enfileira + stdout; não reentra no write)
            self.emit(Event(catalog.EVENTS_FLUSH_FAILED, level="error", status="error",
                            data={"count": len(batch)}, error=e))

    async def _maybe_cleanup(self) -> None:
        if self.cfg.events_retention_days <= 0:
            return
        now = time.monotonic()
        if now - self._last_cleanup < self.cfg.events_cleanup_interval_s:
            return
        self._last_cleanup = now
        try:
            from . import store

            removed = await asyncio.to_thread(store.cleanup, self.cfg.events_retention_days)
            if removed:
                self.emit(Event(catalog.EVENTS_CLEANUP, level="debug",
                                data={"removed": removed, "retention_days": self.cfg.events_retention_days}))
        except Exception as e:
            _log.warning("cleanup de eventos falhou: %s", e)

    async def stop(self) -> None:
        if not self._running:
            return
        self.emit(Event(catalog.BUS_STOPPED, level="debug", data={"dropped": self._dropped}))
        self._running = False
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=self.cfg.events_flush_interval_s * 4 + 5)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                self._task.cancel()

    # ----- introspecção -----
    def stats(self) -> dict:
        return {
            "enabled": self.cfg.events_enabled,
            "running": self._running,
            "queued": self._q.qsize(),
            "dropped": self._dropped,
            "to_db": self._db is not None,
            "to_stdout": self._stdout is not None,
        }


# singleton do processo
bus = EventBus()
