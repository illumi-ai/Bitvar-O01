"""Persistência opcional das análises de academia.

O vídeo bruto nunca é persistido: somente o resultado estruturado, a narrativa e
o WAV opcional. Ausência do pool vira no-op, preservando a degradação graciosa.
"""

from __future__ import annotations

from psycopg.types.json import Json

from app import db
from app.settings import settings

DDL = """
CREATE TABLE IF NOT EXISTS academia_analyses (
    id                    BIGSERIAL    PRIMARY KEY,
    exercise              TEXT         NOT NULL,
    methodology_version   TEXT         NOT NULL,
    practitioner_id       TEXT,
    practitioner_name     TEXT,
    capture_angle         TEXT         NOT NULL,
    analysis_status       TEXT         NOT NULL,
    result_json           JSONB        NOT NULL,
    audio_wav             BYTEA,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
);
-- Migrações idempotentes para instalações que receberam versões preliminares.
ALTER TABLE academia_analyses ADD COLUMN IF NOT EXISTS audio_wav BYTEA;
ALTER TABLE academia_analyses ADD COLUMN IF NOT EXISTS practitioner_id TEXT;
ALTER TABLE academia_analyses ADD COLUMN IF NOT EXISTS practitioner_name TEXT;
ALTER TABLE academia_analyses ADD COLUMN IF NOT EXISTS capture_angle TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE academia_analyses ADD COLUMN IF NOT EXISTS analysis_status TEXT NOT NULL DEFAULT 'complete';
CREATE INDEX IF NOT EXISTS ix_academia_created_at
    ON academia_analyses (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_academia_practitioner
    ON academia_analyses (practitioner_id, created_at DESC)
    WHERE practitioner_id IS NOT NULL;
"""


def available() -> bool:
    """Indica se o processo ao menos inicializou o pool compartilhado."""
    return db._pool is not None  # noqa: SLF001


def init_schema() -> None:
    if db._pool is None:  # noqa: SLF001 - pool compartilhado intencionalmente
        return
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        conn.execute(DDL)


def save(
    *,
    exercise: str,
    methodology_version: str,
    practitioner_id: str | None,
    practitioner_name: str | None,
    capture_angle: str,
    analysis_status: str,
    result_json: dict,
    audio_wav: bytes | None = None,
) -> int | None:
    if db._pool is None:  # noqa: SLF001
        return None
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        row = conn.execute(
            """INSERT INTO academia_analyses
                   (exercise, methodology_version, practitioner_id,
                    practitioner_name, capture_angle, analysis_status,
                    result_json, audio_wav)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                exercise,
                methodology_version,
                practitioner_id,
                practitioner_name,
                capture_angle,
                analysis_status,
                Json(result_json),
                audio_wav,
            ),
        ).fetchone()
    return row["id"] if row else None


def list_analyses(limit: int = 20, offset: int = 0) -> list[dict]:
    """Resumo paginado sem áudio e sem o JSON completo."""
    if db._pool is None:  # noqa: SLF001
        return []
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        return conn.execute(
            """SELECT id, exercise, methodology_version, practitioner_id,
                      practitioner_name, capture_angle, analysis_status, created_at,
                      result_json->'metrics'->'movement'->>'complete_repetitions'
                          AS complete_repetitions,
                      result_json->'metrics'->'weighted_execution_score'->>'score'
                          AS execution_score,
                      result_json->'metrics'->>'priority_improvement'
                          AS priority_improvement,
                      (audio_wav IS NOT NULL) AS has_audio
               FROM academia_analyses
               ORDER BY created_at DESC
               LIMIT %s OFFSET %s""",
            (limit, offset),
        ).fetchall()


def get_analysis(analysis_id: int) -> dict | None:
    if db._pool is None:  # noqa: SLF001
        return None
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        return conn.execute(
            """SELECT id, exercise, methodology_version, practitioner_id,
                      practitioner_name, capture_angle, analysis_status,
                      result_json, created_at,
                      (audio_wav IS NOT NULL) AS has_audio
               FROM academia_analyses WHERE id = %s""",
            (analysis_id,),
        ).fetchone()


def get_audio(analysis_id: int) -> bytes | None:
    if db._pool is None:  # noqa: SLF001
        return None
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT audio_wav FROM academia_analyses WHERE id = %s",
            (analysis_id,),
        ).fetchone()
    if not row or row["audio_wav"] is None:
        return None
    return bytes(row["audio_wav"])


def delete_analysis(analysis_id: int) -> bool:
    """Exclui resultado e áudio do registro; o vídeo bruto nunca foi armazenado."""
    if db._pool is None:  # noqa: SLF001
        return False
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        row = conn.execute(
            "DELETE FROM academia_analyses WHERE id = %s RETURNING id",
            (analysis_id,),
        ).fetchone()
    return row is not None


def practitioner_progress(practitioner_id: str, limit: int = 20) -> list[dict]:
    """Série longitudinal mínima para um identificador opaco informado pelo usuário.

    O endpoint correspondente não é publicado no MVP anônimo; a consulta existe
    como extensão do motor para futura autenticação/multiusuário (RF-013).
    """
    if db._pool is None:  # noqa: SLF001
        return []
    with db._pool.connection(timeout=settings.db_connect_timeout) as conn:  # noqa: SLF001
        return conn.execute(
            """SELECT id, exercise, methodology_version, analysis_status, created_at,
                      result_json->'metrics'->'weighted_execution_score'->>'score'
                          AS execution_score,
                      result_json->'metrics'->'movement'->>'complete_repetitions'
                          AS complete_repetitions
               FROM academia_analyses
               WHERE practitioner_id = %s
               ORDER BY created_at ASC
               LIMIT %s""",
            (practitioner_id, limit),
        ).fetchall()
