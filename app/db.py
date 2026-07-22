"""Pool psycopg3, schema idempotente e persistência das análises."""

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from app.settings import settings

_pool: ConnectionPool | None = None

DDL = """
CREATE TABLE IF NOT EXISTS analises (
    id                   BIGSERIAL    PRIMARY KEY,
    dados                JSONB        NOT NULL,
    estatisticas         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    descricao_acessivel  TEXT         NOT NULL,
    criado_em            TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_analises_criado_em ON analises (criado_em DESC);
"""


def init_pool() -> None:
    """Cria o pool sem bloquear o boot se o Postgres ainda não respondeu."""
    global _pool
    _pool = ConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    _pool.open(wait=False)  # reconecta sozinho; /ready reflete o estado real


def init_schema() -> None:
    # timeout limitado: não trava o boot por 30s (default do pool) se o DB
    # ainda não respondeu — coerente com init_pool (boot não-bloqueante).
    with _pool.connection(timeout=settings.db_connect_timeout) as conn:
        conn.execute(DDL)


def ping() -> bool:
    try:
        with _pool.connection(timeout=settings.db_connect_timeout) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def salvar_analise(dados: dict, estatisticas: dict, descricao: str) -> dict:
    with _pool.connection(timeout=settings.db_connect_timeout) as conn:
        return conn.execute(
            """INSERT INTO analises (dados, estatisticas, descricao_acessivel)
               VALUES (%s, %s, %s)
               RETURNING id, criado_em""",
            (Json(dados), Json(estatisticas), descricao),
        ).fetchone()


def listar_analises(limit: int = 20, offset: int = 0) -> list[dict]:
    with _pool.connection(timeout=settings.db_connect_timeout) as conn:
        return conn.execute(
            """SELECT id, dados, estatisticas, descricao_acessivel, criado_em
               FROM analises ORDER BY criado_em DESC LIMIT %s OFFSET %s""",
            (limit, offset),
        ).fetchall()


def obter_analise(analise_id: int) -> dict | None:
    with _pool.connection(timeout=settings.db_connect_timeout) as conn:
        return conn.execute(
            """SELECT id, dados, estatisticas, descricao_acessivel, criado_em
               FROM analises WHERE id = %s""",
            (analise_id,),
        ).fetchone()


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.close()
        _pool = None  # evita reuso de pool fechado (guardas `is None` voltam a valer)
