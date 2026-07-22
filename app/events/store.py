"""Persistência dos eventos na tabela ``events`` (auditoria consultável).

Reusa o pool de :mod:`app.db` com timeout curto (best-effort: se o DB cair, o
worker apenas loga; os eventos seguem no stdout). Todas as queries são
parametrizadas.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from psycopg.types.json import Json

from app import db
from app.settings import settings

DDL = """
CREATE TABLE IF NOT EXISTS events (
    id              BIGSERIAL    PRIMARY KEY,
    event_id        UUID         NOT NULL,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    category        TEXT         NOT NULL,
    name            TEXT         NOT NULL,
    level           TEXT         NOT NULL,
    status          TEXT,
    correlation_id  TEXT,
    actor           TEXT,
    source          TEXT,
    method          TEXT,
    path            TEXT,
    status_code     INT,
    duration_ms     DOUBLE PRECISION,
    message         TEXT,
    data            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts        ON events (ts DESC);
CREATE INDEX IF NOT EXISTS ix_events_category  ON events (category, ts DESC);
CREATE INDEX IF NOT EXISTS ix_events_name      ON events (name, ts DESC);
CREATE INDEX IF NOT EXISTS ix_events_level     ON events (level, ts DESC);
CREATE INDEX IF NOT EXISTS ix_events_corr      ON events (correlation_id);
CREATE INDEX IF NOT EXISTS ix_events_status    ON events (status_code);
"""

# ordem idêntica a Event.to_row()
INSERT_SQL = """
INSERT INTO events
  (event_id, ts, category, name, level, status, correlation_id, actor, source,
   method, path, status_code, duration_ms, message, data, error)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""

_SELECT_COLS = (
    "id, event_id, ts, category, name, level, status, correlation_id, actor, "
    "source, method, path, status_code, duration_ms, message, data, error"
)


def init_schema() -> None:
    if db._pool is None:  # noqa: SLF001
        return
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        conn.execute(DDL)


def insert_batch(rows: list[tuple]) -> int:
    """Grava um lote. ``rows`` são tuplas de :meth:`Event.to_row`."""
    if db._pool is None or not rows:  # noqa: SLF001
        return 0
    prepared = [(*r[:14], Json(r[14]), r[15]) for r in rows]  # data (idx 14) -> JSONB
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        with conn.cursor() as cur:
            cur.executemany(INSERT_SQL, prepared)
    return len(rows)


def _filters(category=None, name=None, level=None, status=None, correlation_id=None,
             actor=None, status_code=None, since=None, until=None, q=None):
    conds, params = [], []
    def add(sql, val):
        conds.append(sql); params.append(val)
    if category: add("category = %s", category)
    if name: add("name = %s", name)
    if level: add("level = %s", level)
    if status: add("status = %s", status)
    if correlation_id: add("correlation_id = %s", correlation_id)
    if actor: add("actor = %s", actor)
    if status_code is not None: add("status_code = %s", status_code)
    if since: add("ts >= %s", since)
    if until: add("ts <= %s", until)
    if q:
        conds.append("(message ILIKE %s OR name ILIKE %s OR path ILIKE %s)")
        like = f"%{q}%"; params += [like, like, like]
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def query_events(*, limit=50, offset=0, **filters) -> tuple[int, list[dict]]:
    if db._pool is None:  # noqa: SLF001
        return 0, []
    where, params = _filters(**filters)
    try:
        with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
            total = conn.execute(f"SELECT count(*) AS c FROM events{where}", params).fetchone()["c"]
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM events{where} ORDER BY ts DESC, id DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        return total, rows
    except Exception:  # DB indisponível → leitura degradada (vazia), sem 500
        return 0, []


def timeline(correlation_id: str, limit: int = 500) -> list[dict]:
    if db._pool is None:  # noqa: SLF001
        return []
    try:
        with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
            return conn.execute(
                f"SELECT {_SELECT_COLS} FROM events WHERE correlation_id = %s "
                "ORDER BY ts ASC, id ASC LIMIT %s",
                [correlation_id, limit],
            ).fetchall()
    except Exception:
        return []


def stats(since: datetime | None = None) -> dict:
    if db._pool is None:  # noqa: SLF001
        return {}
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        return _stats_query(since)
    except Exception:  # DB indisponível → stats vazios, sem 500
        return {}


def _stats_query(since: datetime) -> dict:
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        base = "FROM events WHERE ts >= %s"
        total = conn.execute(f"SELECT count(*) c {base}", [since]).fetchone()["c"]
        by_cat = {r["category"]: r["c"] for r in conn.execute(
            f"SELECT category, count(*) c {base} GROUP BY category ORDER BY c DESC", [since]).fetchall()}
        by_lvl = {r["level"]: r["c"] for r in conn.execute(
            f"SELECT level, count(*) c {base} GROUP BY level", [since]).fetchall()}
        top = [{"name": r["name"], "count": r["c"]} for r in conn.execute(
            f"SELECT name, count(*) c {base} GROUP BY name ORDER BY c DESC LIMIT 12", [since]).fetchall()]
        errors = conn.execute(f"SELECT count(*) c {base} AND level = 'error'", [since]).fetchone()["c"]
        http = conn.execute(
            f"SELECT count(*) c, avg(duration_ms) avg_ms {base} AND name = 'http.request'", [since]
        ).fetchone()
        # mesma população do denominador (http.request) p/ a taxa ficar em [0,1];
        # http.request.failed é sinal de alerta, não entra no cálculo da taxa.
        http_err = conn.execute(
            f"SELECT count(*) c {base} AND name = 'http.request' AND status_code >= 500", [since]
        ).fetchone()["c"]
    http_count = http["c"] or 0
    return {
        "since": since, "total": total, "by_category": by_cat, "by_level": by_lvl,
        "top_events": top, "errors": errors, "http_requests": http_count,
        "http_error_rate": round(http_err / http_count, 4) if http_count else 0.0,
        "avg_http_ms": round(http["avg_ms"], 1) if http["avg_ms"] is not None else None,
    }


def cleanup(retention_days: int) -> int:
    if db._pool is None or retention_days <= 0:  # noqa: SLF001
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        cur = conn.execute("DELETE FROM events WHERE ts < %s", [cutoff])
        return cur.rowcount
