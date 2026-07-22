"""Persistência opcional das análises de tênis (blueprint §09 + spec E1 do Caio).

Grava uma linha por análise quando o Postgres existente está disponível (o pool
de :mod:`app.db`, não uma conexão própria) — e agora também **recupera** o
histórico, para o Caio "salvar e mandar de volta" (feedback 13/06). Tudo é
tolerante a DB ausente: sem pool, escrita/leitura viram no-op (None / lista vazia)
em vez de erro, coerente com o modelo de degradação graciosa do projeto.

O áudio (WAV) fica em coluna ``BYTEA`` separada — nunca dentro do ``result_json``
JSONB, que infla a linha e degrada a listagem.
"""

from __future__ import annotations

from psycopg.types.json import Json

from app import db
from app.settings import settings

DDL = """
CREATE TABLE IF NOT EXISTS tennis_analyses (
    id           BIGSERIAL    PRIMARY KEY,
    gender       TEXT         NOT NULL,
    mode         TEXT         NOT NULL,
    result_json  JSONB        NOT NULL,
    audio_wav    BYTEA,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tennis_created_at ON tennis_analyses (created_at DESC);
-- migração idempotente p/ bases criadas antes da coluna de áudio:
ALTER TABLE tennis_analyses ADD COLUMN IF NOT EXISTS audio_wav BYTEA;
"""


def init_schema() -> None:
    """Cria a tabela; chamado no lifespan, tolerante a DB ausente."""
    if db._pool is None:  # noqa: SLF001 - reuso intencional do pool da app
        return
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        conn.execute(DDL)


def save(gender: str, mode: str, result_json: dict, audio_wav: bytes | None = None) -> int | None:
    """Grava a análise (e o áudio, se houver) e retorna o id; None se não houver pool."""
    if db._pool is None:  # noqa: SLF001
        return None
    # timeout curto: com DB fora, falha rápido e vira aviso (não trava o worker ~30s)
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        row = conn.execute(
            """INSERT INTO tennis_analyses (gender, mode, result_json, audio_wav)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (gender, mode, Json(result_json), audio_wav),
        ).fetchone()
    return row["id"] if row else None


def list_analyses(limit: int = 20, offset: int = 0) -> list[dict]:
    """Histórico paginado (resumo leve, sem o WAV). Lista vazia se não houver pool."""
    if db._pool is None:  # noqa: SLF001
        return []
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        return conn.execute(
            """SELECT id, gender, mode, created_at,
                      result_json->'metrics'->>'shot_identified'  AS shot,
                      result_json->'metrics'->>'action_phase'     AS action_phase,
                      result_json->'metrics'->>'key_improvement'  AS key_improvement,
                      result_json->'metrics'->'weighted_performance_score'->>'score' AS weighted_score,
                      (audio_wav IS NOT NULL)                      AS has_audio
               FROM tennis_analyses
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
            """SELECT id, gender, mode, result_json, created_at,
                      (audio_wav IS NOT NULL) AS has_audio
               FROM tennis_analyses WHERE id = %s""",
            (analysis_id,),
        ).fetchone()


def get_audio(analysis_id: int) -> bytes | None:
    """Bytes do WAV de uma análise. None se ausente/sem áudio/sem pool."""
    if db._pool is None:  # noqa: SLF001
        return None
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT audio_wav FROM tennis_analyses WHERE id = %s", (analysis_id,)
        ).fetchone()
    if not row or row["audio_wav"] is None:
        return None
    return bytes(row["audio_wav"])  # memoryview → bytes
