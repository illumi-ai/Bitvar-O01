"""Persistência opcional das análises de academia (espelha ``app/tennis/store.py``).

Grava uma linha por análise quando o Postgres existente está disponível (o
pool de :mod:`app.db`, não uma conexão própria). Tudo é tolerante a DB
ausente: sem pool, escrita/leitura viram no-op (None / lista vazia) em vez de
erro — mesmo modelo de degradação graciosa do tênis. Diferença de config:
aqui a persistência é opt-in (``ACADEMIA_PERSIST`` default ``false``), não
opt-out.

O áudio (WAV) fica em coluna ``BYTEA`` separada de ``result_json`` (JSONB) —
não misturar, infla a listagem.
"""

from __future__ import annotations

from psycopg.types.json import Json

from app import db
from app.settings import settings

DDL = """
CREATE TABLE IF NOT EXISTS academia_analyses (
    id           BIGSERIAL    PRIMARY KEY,
    student_name TEXT,
    result_json  JSONB        NOT NULL,
    audio_wav    BYTEA,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_academia_created_at ON academia_analyses (created_at DESC);
"""


def init_schema() -> None:
    """Cria a tabela; chamado no lifespan, tolerante a DB ausente."""
    if db._pool is None:  # noqa: SLF001 - reuso intencional do pool da app
        return
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        conn.execute(DDL)


def save(student_name: str | None, result_json: dict, audio_wav: bytes | None = None) -> int | None:
    """Grava a análise (e o áudio, se houver) e retorna o id; None se não houver pool."""
    if db._pool is None:  # noqa: SLF001
        return None
    # timeout curto: com DB fora, falha rápido e vira aviso (não trava o worker ~30s)
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        row = conn.execute(
            """INSERT INTO academia_analyses (student_name, result_json, audio_wav)
               VALUES (%s, %s, %s) RETURNING id""",
            (student_name, Json(result_json), audio_wav),
        ).fetchone()
    return row["id"] if row else None


def list_analyses(limit: int = 20, offset: int = 0) -> list[dict]:
    """Histórico paginado (resumo leve, sem o WAV). Lista vazia se não houver pool."""
    if db._pool is None:  # noqa: SLF001
        return []
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        return conn.execute(
            """SELECT id, student_name, created_at,
                      result_json->'metrics'->>'exercicio_identificado' AS exercicio,
                      result_json->'metrics'->>'veredito'                AS veredito,
                      result_json->'metrics'->>'risco_lesao'             AS risco_lesao,
                      (audio_wav IS NOT NULL)                            AS has_audio
               FROM academia_analyses
               ORDER BY created_at DESC
               LIMIT %s OFFSET %s""",
            (limit, offset),
        ).fetchall()


def get_analysis(analysis_id: int) -> dict | None:
    """Análise completa por id (sem os bytes do áudio). None se ausente/sem pool."""
    if db._pool is None:  # noqa: SLF001
        return None
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        return conn.execute(
            """SELECT id, student_name, result_json, created_at,
                      (audio_wav IS NOT NULL) AS has_audio
               FROM academia_analyses WHERE id = %s""",
            (analysis_id,),
        ).fetchone()


def get_audio(analysis_id: int) -> bytes | None:
    """Bytes do WAV de uma análise. None se ausente/sem áudio/sem pool."""
    if db._pool is None:  # noqa: SLF001
        return None
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT audio_wav FROM academia_analyses WHERE id = %s", (analysis_id,)
        ).fetchone()
    if not row or row["audio_wav"] is None:
        return None
    return bytes(row["audio_wav"])  # memoryview → bytes
