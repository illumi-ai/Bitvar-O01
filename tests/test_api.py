"""Testes da API que não dependem do banco."""

from fastapi.testclient import TestClient


def _client(monkeypatch):
    # evita abrir pool real no import; lifespan tenta init_schema e ignora falha
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:x@localhost:5432/x")
    from app.main import app

    return TestClient(app)


def test_health(monkeypatch):
    client = _client(monkeypatch)
    with client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_sem_db_retorna_503(monkeypatch):
    client = _client(monkeypatch)
    with client:
        r = client.get("/ready")
    assert r.status_code == 503
