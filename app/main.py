"""API FastAPI: futebol legado, tênis, academia por vídeo e eventos.

Inclui um sistema de eventos (auditoria + observabilidade): um middleware ASGI
rastreia toda requisição HTTP e o pipeline emite eventos de domínio, gravados na
tabela ``events`` e no stdout.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from bitvar import AnalisadorEsportivo  # usa a biblioteca existente, intocada
from app import db
from app.events import catalog, emit
from app.events import store as events_store
from app.events.bus import bus as events_bus
from app.events.middleware import EventMiddleware
from app.events.router import router as events_router
from app.schemas import AnaliseOut, PartidaIn
from app.academia import store as academia_store
from app.academia.audio import cleanup_stale_voice_tempfiles
from app.academia.config import academia_settings
from app.academia.middleware import VideoUploadGuard
from app.academia.router import router as academia_router
from app.tennis import store as tennis_store
from app.tennis.config import tennis_settings
from app.tennis.router import router as tennis_router

analisador = AnalisadorEsportivo()  # stateless -> singleton de módulo


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    # Uma migração opcional não pode impedir as demais verticais de tentar seu
    # próprio schema. O app continua degradado e /ready reflete o estado real.
    try:
        db.init_schema()
    except Exception as exc:
        # Uma única espera de conexão mantém o boot fail-fast quando o banco
        # inteiro está fora; não repetir o mesmo timeout para cada vertical.
        emit(
            catalog.SCHEMA_INIT,
            level="warning",
            status="error",
            data={"schema": "legacy", "error_type": type(exc).__name__},
        )
    else:
        # Com conexão confirmada, falha de uma tabela opcional não bloqueia as
        # outras (por exemplo, tênis não impede Academia).
        for schema_name, initializer in (
            ("tennis", tennis_store.init_schema),
            ("academia", academia_store.init_schema),
            ("events", events_store.init_schema),
        ):
            try:
                initializer()
            except Exception as exc:
                emit(
                    catalog.SCHEMA_INIT,
                    level="warning",
                    status="error",
                    data={"schema": schema_name, "error_type": type(exc).__name__},
                )
    await events_bus.start()
    removed_voice_temps, failed_voice_temp_removals = (
        cleanup_stale_voice_tempfiles()
    )
    if removed_voice_temps or failed_voice_temp_removals:
        emit(
            catalog.ACADEMIA_WARNING,
            level=(
                "warning" if failed_voice_temp_removals else "info"
            ),
            status=(
                "error" if failed_voice_temp_removals else "ok"
            ),
            data={
                "stage": "voice_temp_startup_cleanup",
                "removed_count": removed_voice_temps,
                "failed_count": failed_voice_temp_removals,
            },
        )
    emit(catalog.APP_STARTUP, data={"version": "0.1.0"})
    yield
    emit(catalog.APP_SHUTDOWN)
    await events_bus.stop()
    db.close_pool()


app = FastAPI(title="Bitvar O01 API", version="0.1.0", lifespan=lifespan)
app.include_router(tennis_router)
app.include_router(academia_router)
app.include_router(events_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    """Impede cache também nos 422 automáticos com dados da vertical Academia."""
    response = await request_validation_exception_handler(request, exc)
    if request.url.path.startswith("/academia/"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/")
def raiz():
    return {
        "servico": "Bitvar O01 API",
        "versao": "0.1.0",
        "docs": "/docs",
        "tenis": "/tennis/",
        "academia": "/academia/",
        "eventos": "/events/ui",
    }


@app.get("/health")
def health():
    """Liveness — não toca no banco."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness — verifica o banco com SELECT 1."""
    if db.ping():
        return {"status": "ready", "db": "up"}
    return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})


@app.post("/analises", response_model=AnaliseOut, status_code=201)
def criar_analise(partida: PartidaIn):
    resultado = analisador.analisar_partida(partida.model_dump())
    try:
        row = db.salvar_analise(
            resultado.dados, resultado.estatisticas, resultado.descricao_acessivel
        )
    except Exception as e:
        emit(catalog.SOCCER_ANALISE_FAILED, level="error", status="error", error=e,
             message="falha ao salvar análise de futebol")
        raise HTTPException(503, "Banco de dados temporariamente indisponível.")
    emit(catalog.SOCCER_ANALISE_CREATED, data={
        "id": row["id"], "vencedor": resultado.estatisticas.get("vencedor"),
        "empate": resultado.estatisticas.get("empate"),
    })
    return AnaliseOut(
        id=row["id"],
        dados=resultado.dados,
        estatisticas=resultado.estatisticas,
        descricao_acessivel=resultado.descricao_acessivel,
        criado_em=row["criado_em"],
    )


@app.get("/analises", response_model=list[AnaliseOut])
def listar_analises(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    try:
        rows = db.listar_analises(limit, offset)
    except Exception as e:
        emit(catalog.SOCCER_ANALISE_FAILED, level="error", status="error", error=e)
        raise HTTPException(503, "Banco de dados temporariamente indisponível.")
    emit(catalog.SOCCER_ANALISE_LISTED, data={"count": len(rows), "limit": limit, "offset": offset})
    return [AnaliseOut(**row) for row in rows]


@app.get("/analises/{analise_id}", response_model=AnaliseOut)
def obter_analise(analise_id: int):
    try:
        row = db.obter_analise(analise_id)
    except Exception as e:
        emit(catalog.SOCCER_ANALISE_FAILED, level="error", status="error", error=e)
        raise HTTPException(503, "Banco de dados temporariamente indisponível.")
    if row is None:
        raise HTTPException(404, "Análise não encontrada.")
    emit(catalog.SOCCER_ANALISE_FETCHED, data={"id": analise_id})
    return AnaliseOut(**row)


# Guards ficam antes do parser multipart: o áudio curto tem um teto próprio e
# nunca herda o limite de centenas de MB dos vídeos. O evento permanece como
# camada mais externa para medir inclusive rejeições antecipadas.
voice_guarded_app = VideoUploadGuard(
    app,
    guarded_paths={"/academia/transcribe-target"},
    max_body_bytes=academia_settings.voice_max_request_body_bytes,
    max_concurrent_uploads=(
        academia_settings.academia_max_concurrent_transcriptions
    ),
    too_large_detail="gravação acima do limite permitido.",
    busy_detail=(
        "o transcritor está ocupado; tente novamente em alguns instantes."
    ),
)
guarded_app = VideoUploadGuard(
    voice_guarded_app,
    guarded_paths={"/academia/analyze", "/tennis/analyze"},
    max_body_bytes=max(
        academia_settings.max_request_body_bytes,
        tennis_settings.max_upload_bytes
        + academia_settings.academia_request_body_overhead_mb * 1024 * 1024,
    ),
    max_concurrent_uploads=academia_settings.academia_max_concurrent_analyses,
)
asgi_app = EventMiddleware(guarded_app)
