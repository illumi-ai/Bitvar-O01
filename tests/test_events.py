"""Testes do sistema de eventos — sem rede e sem DB (pool ausente)."""

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost:5432/x")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.events import bus as bus_mod  # noqa: E402
from app.events import catalog, emit, set_context  # noqa: E402
from app.events.bus import EventBus  # noqa: E402
from app.events.catalog import CATALOG, category_for  # noqa: E402
from app.events.config import EventSettings  # noqa: E402
from app.events.models import Event, scrub  # noqa: E402
from app.events import store  # noqa: E402


# --------------------------------------------------------------------------- #
# scrubbing / modelo                                                          #
# --------------------------------------------------------------------------- #
def test_scrub_redacts_secrets_and_truncates():
    s = scrub({
        "gemini_api_key": "SECRET", "password": "p", "Authorization": "Bearer x",
        "nested": {"token": "t", "ok": 1}, "list": [{"db_password": "z"}],
        "big": "x" * 5000,
    })
    assert s["gemini_api_key"] == "***"
    assert s["password"] == "***"
    assert s["Authorization"] == "***"
    assert s["nested"]["token"] == "***" and s["nested"]["ok"] == 1
    assert s["list"][0]["db_password"] == "***"
    assert s["big"].endswith("[truncado]") and len(s["big"]) < 5000


def test_event_infers_category_and_serializes():
    e = Event("tennis.analyze.completed", data={"mode": "clip"}, duration_ms=12.5, status="ok")
    assert e.category == "tennis"
    row = e.to_row()
    assert len(row) == 16 and row[2] == "tennis" and row[3] == "tennis.analyze.completed"
    log = e.to_log()
    assert log["event"] == "tennis.analyze.completed" and log["category"] == "tennis"
    assert "ts" in log and log["duration_ms"] == 12.5


def test_catalog_categories_consistent():
    assert len(CATALOG) >= 30
    for name, (cat, desc) in CATALOG.items():
        assert category_for(name) == cat, name
        assert desc


def test_emit_never_raises():
    class Bad:
        def __str__(self):
            raise RuntimeError("boom")
    # nem dado problemático nem nada deve propagar exceção
    emit("system.test.weird", data={"obj": Bad()}, error=ValueError("x"))


# --------------------------------------------------------------------------- #
# bus                                                                          #
# --------------------------------------------------------------------------- #
def test_bus_drops_when_full():
    b = EventBus(EventSettings(events_to_stdout=False, events_queue_max=2))
    for i in range(5):
        b.emit(Event(f"system.test.{i}"))
    st = b.stats()
    assert st["queued"] == 2 and st["dropped"] == 3


def test_bus_flush_writes_batch():
    b = EventBus(EventSettings(events_to_stdout=False, events_batch_max=10))
    written = []

    class FakeDb:
        def write(self, batch):
            written.extend(batch)
            return len(batch)

    b._db = FakeDb()
    for i in range(3):
        b.emit(Event(f"system.test.{i}"))
    asyncio.run(b._flush())
    assert len(written) == 3
    assert b._q.qsize() == 0


def test_bus_flush_survives_db_failure():
    b = EventBus(EventSettings(events_to_stdout=False))

    class BoomDb:
        def write(self, batch):
            raise RuntimeError("db down")

    b._db = BoomDb()
    b.emit(Event("system.test.x"))
    asyncio.run(b._flush())  # não deve levantar


def test_emit_uses_context(monkeypatch):
    got = []
    monkeypatch.setattr(bus_mod, "emit", lambda ev: got.append(ev))
    set_context(correlation_id="cid-1", actor="9.9.9.9", method="GET", path="/x")
    emit("system.test.ctx")
    assert got[-1].correlation_id == "cid-1" and got[-1].actor == "9.9.9.9"


# --------------------------------------------------------------------------- #
# correlação propaga para o threadpool                                         #
# --------------------------------------------------------------------------- #
def test_correlation_propagates_to_threadpool():
    from app.events.context import get_correlation_id

    async def main():
        set_context(correlation_id="abc123")
        return await run_in_threadpool(get_correlation_id)

    assert asyncio.run(main()) == "abc123"


# --------------------------------------------------------------------------- #
# store (sem DB): filtros e degradação graciosa                                #
# --------------------------------------------------------------------------- #
def test_filters_build_conditions():
    where, params = store._filters(category="http", level="error", status_code=500, q="boom")
    assert "category = %s" in where and "level = %s" in where and "status_code = %s" in where
    assert "ILIKE" in where
    assert "http" in params and "error" in params and 500 in params and "%boom%" in params


def test_store_without_db_is_empty():
    # pool não inicializado neste processo de teste → respostas vazias, sem erro
    assert store.query_events(limit=10) == (0, [])
    assert store.timeline("nope") == []
    assert store.insert_batch([]) == 0


# --------------------------------------------------------------------------- #
# middleware + endpoints                                                       #
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    from app.main import asgi_app  # EventMiddleware externo (rastreia tudo)
    return TestClient(asgi_app, raise_server_exceptions=False)  # sem 'with' → sem lifespan/DB


def test_request_id_present_on_500():
    # X-Request-ID deve aparecer mesmo em respostas 500 (middleware externo ao
    # ServerErrorMiddleware) — regressão do achado da revisão.
    from fastapi import FastAPI

    from app.events.middleware import EventMiddleware

    mini = FastAPI()

    @mini.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    c = TestClient(EventMiddleware(mini), raise_server_exceptions=False)
    r = c.get("/boom")
    assert r.status_code == 500
    assert r.headers.get("x-request-id")


def test_middleware_sets_request_id_and_emits(client, monkeypatch):
    got = []
    monkeypatch.setattr(bus_mod, "emit", lambda ev: got.append(ev))
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers.get("x-request-id")
    http = [e for e in got if e.name == catalog.HTTP_REQUEST]
    assert http and http[-1].status_code == 200 and http[-1].method == "GET"
    assert http[-1].correlation_id  # correlacionado


def test_middleware_skips_health(client, monkeypatch):
    got = []
    monkeypatch.setattr(bus_mod, "emit", lambda ev: got.append(ev))
    r = client.get("/health")
    assert r.status_code == 200
    assert not [e for e in got if e.name == catalog.HTTP_REQUEST]  # /health é pulado
    assert r.headers.get("x-request-id")  # mas ainda recebe request id


def test_events_endpoints_without_db(client):
    assert client.get("/events").json() == {"total": 0, "limit": 50, "offset": 0, "items": []}
    cat = client.get("/events/catalog").json()
    assert len(cat) >= 30 and all({"name", "category", "description"} <= set(i) for i in cat)
    stats = client.get("/events/stats").json()
    assert stats["total"] == 0 and "bus" in stats and stats["bus"]["to_stdout"] in (True, False)
    assert client.get("/events/ui").status_code == 200
