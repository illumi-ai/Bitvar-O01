"""API de consulta de eventos (auditoria + observabilidade).

* ``GET /events``                 — lista com filtros + paginação
* ``GET /events/stats``           — agregados + estado do event bus
* ``GET /events/catalog``         — todos os eventos possíveis (taxonomia)
* ``GET /events/ui``              — dashboard
* ``GET /events/{correlation_id}``— linha do tempo de uma requisição
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from . import catalog
from .bus import bus
from .models import BusStats, CatalogItem, EventListOut, EventOut, EventStats
from .store import query_events, stats, timeline

router = APIRouter(prefix="/events", tags=["events"])

_UI = Path(__file__).resolve().parent.parent / "static" / "events" / "index.html"


def _to_out(row: dict) -> EventOut:
    return EventOut(**{**row, "event_id": str(row["event_id"])})


@router.get("", response_model=EventListOut)
@router.get("/", response_model=EventListOut, include_in_schema=False)
def list_events(
    category: str | None = None,
    name: str | None = None,
    level: str | None = None,
    status: str | None = None,
    correlation_id: str | None = None,
    actor: str | None = None,
    status_code: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = Query(None, description="Busca em message/name/path."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    total, rows = query_events(
        limit=limit, offset=offset, category=category, name=name, level=level,
        status=status, correlation_id=correlation_id, actor=actor,
        status_code=status_code, since=since, until=until, q=q,
    )
    return EventListOut(total=total, limit=limit, offset=offset, items=[_to_out(r) for r in rows])


@router.get("/stats", response_model=EventStats)
def event_stats(hours: int = Query(24, ge=1, le=24 * 90)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    s = stats(since)
    bs = bus.stats()
    return EventStats(
        since=s.get("since", since),
        total=s.get("total", 0),
        by_category=s.get("by_category", {}),
        by_level=s.get("by_level", {}),
        top_events=s.get("top_events", []),
        errors=s.get("errors", 0),
        http_requests=s.get("http_requests", 0),
        http_error_rate=s.get("http_error_rate", 0.0),
        avg_http_ms=s.get("avg_http_ms"),
        bus=BusStats(**bs),
    )


@router.get("/catalog", response_model=list[CatalogItem])
def event_catalog():
    return [
        CatalogItem(name=n, category=c, description=d)
        for n, (c, d) in sorted(catalog.CATALOG.items())
    ]


@router.get("/ui", include_in_schema=False)
def ui():
    return FileResponse(_UI, media_type="text/html")


@router.get("/{correlation_id}", response_model=list[EventOut])
def event_timeline(correlation_id: str, limit: int = Query(500, ge=1, le=2000)):
    return [_to_out(r) for r in timeline(correlation_id, limit)]
