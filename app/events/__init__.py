"""Sistema de eventos do BitVar — auditoria + observabilidade.

Uso típico::

    from app.events import emit, catalog
    emit(catalog.TENNIS_ANALYZE_COMPLETED, duration_ms=1234, data={"mode": "clip"})

``emit()`` preenche ``correlation_id``/``actor`` a partir do contexto da
requisição, redige segredos, loga no stdout e enfileira para o DB — sem nunca
levantar exceção. Veja :mod:`app.events.catalog` para todos os eventos possíveis.
"""

from __future__ import annotations

from . import catalog
from .bus import bus
from .context import get_context, get_correlation_id, set_context
from .models import Event

__all__ = ["emit", "bus", "catalog", "set_context", "get_context", "get_correlation_id", "Event"]


def emit(
    name: str,
    *,
    level: str = "info",
    status: str | None = None,
    message: str | None = None,
    data: dict | None = None,
    error: object | None = None,
    duration_ms: float | None = None,
    source: str | None = None,
    category: str | None = None,
    correlation_id: str | None = None,
    actor: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
) -> None:
    """Emite um evento. Não-bloqueante e à prova de falhas (nunca levanta)."""
    try:
        ctx = get_context()
        event = Event(
            name=name,
            category=category or "",
            level=level,
            status=status,
            correlation_id=correlation_id if correlation_id is not None else ctx.correlation_id,
            actor=actor if actor is not None else ctx.actor,
            source=source,
            method=method if method is not None else ctx.method,
            path=path if path is not None else ctx.path,
            status_code=status_code,
            duration_ms=duration_ms,
            message=message,
            data=data or {},
            error=None if error is None else str(error),
        )
        bus.emit(event)
    except Exception:  # pragma: no cover - blindagem
        pass
