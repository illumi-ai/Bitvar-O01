"""API FastAPI: análise de futebol (legado), tênis por vídeo e eventos.

Inclui um sistema de eventos (auditoria + observabilidade): um middleware ASGI
rastreia toda requisição HTTP e o pipeline emite eventos de domínio, gravados na
tabela ``events`` e no stdout.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from bitvar import AnalisadorEsportivo  # usa a biblioteca existente, intocada
from app import db
from app.events import catalog, emit
from app.events import store as events_store
from app.events.bus import bus as events_bus
from app.events.middleware import EventMiddleware
from app.events.router import router as events_router
from app.schemas import AnaliseOut, PartidaIn
from app.tennis import store as tennis_store
from app.tennis.router import router as tennis_router

analisador = AnalisadorEsportivo()  # stateless -> singleton de módulo


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    try:
        db.init_schema()  # tenta criar o schema; se o DB ainda não subiu, segue
        tennis_store.init_schema()  # tabela opcional de análises de tênis
        events_store.init_schema()  # tabela de eventos (auditoria)
    except Exception:
        pass  # /ready ficará 503 até o DB responder
    await events_bus.start()
    emit(catalog.APP_STARTUP, data={"version": "0.1.0"})
    yield
    emit(catalog.APP_SHUTDOWN)
    await events_bus.stop()
    db.close_pool()


app = FastAPI(title="Bitvar O01 API", version="0.1.0", lifespan=lifespan)
app.include_router(tennis_router)
app.include_router(events_router)


@app.get("/")
def raiz():
    return {
        "servico": "Bitvar O01 API",
        "versao": "0.1.0",
        "docs": "/docs",
        "tenis": "/tennis/",
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


# EventMiddleware como camada ASGI mais EXTERNA (fora do ServerErrorMiddleware do
# Starlette) para que o X-Request-ID seja injetado também nas respostas 500 e a
# requisição inteira seja medida/rastreada. Servir este objeto (não `app`).
asgi_app = EventMiddleware(app)
