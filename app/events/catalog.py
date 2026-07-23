"""Catálogo de eventos — a taxonomia de *todos os eventos possíveis*.

Cada evento tem um nome estável ``categoria.objeto.acao`` e uma descrição. O
endpoint ``GET /events/catalog`` expõe esta lista, então o conjunto de eventos
rastreados é enumerável e documentado (não há eventos "fantasma").

Para adicionar um evento: declare a constante aqui, registre em ``CATALOG`` e
emita com :func:`app.events.emit`.
"""

from __future__ import annotations


class Category:
    SYSTEM = "system"
    HTTP = "http"
    TENNIS = "tennis"
    ACADEMIA = "academia"
    GEMINI = "gemini"
    SOCCER = "soccer"
    DB = "db"


class Level:
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    _ORDER = {"debug": 10, "info": 20, "warning": 30, "error": 40}

    @classmethod
    def rank(cls, level: str) -> int:
        return cls._ORDER.get(level, 20)


# ---- system / lifecycle -------------------------------------------------- #
APP_STARTUP = "system.app.startup"
APP_SHUTDOWN = "system.app.shutdown"
BUS_STARTED = "system.events.bus_started"
BUS_STOPPED = "system.events.bus_stopped"
EVENTS_DROPPED = "system.events.dropped"
EVENTS_FLUSH_FAILED = "system.events.flush_failed"
EVENTS_CLEANUP = "system.events.cleanup"
SCHEMA_INIT = "system.db.schema_init"

# ---- http ---------------------------------------------------------------- #
HTTP_REQUEST = "http.request"
HTTP_REQUEST_FAILED = "http.request.failed"

# ---- tennis -------------------------------------------------------------- #
TENNIS_ANALYZE_RECEIVED = "tennis.analyze.received"
TENNIS_UPLOAD_SAVED = "tennis.upload.saved"
TENNIS_UPLOAD_REJECTED = "tennis.upload.rejected"
TENNIS_ROUTE_DECIDED = "tennis.route.decided"
TENNIS_WEIGHTED_SCORE = "tennis.weighted_score.computed"
TENNIS_ANALYZE_COMPLETED = "tennis.analyze.completed"
TENNIS_ANALYZE_FAILED = "tennis.analyze.failed"
TENNIS_PERSISTED = "tennis.persisted"
TENNIS_ANALYSIS_RETRIEVED = "tennis.analysis.retrieved"
TENNIS_ANALYSIS_EXPORTED = "tennis.analysis.exported"
TENNIS_WARNING = "tennis.warning"

# ---- academia -------------------------------------------------------------- #
ACADEMIA_ANALYZE_RECEIVED = "academia.analyze.received"
ACADEMIA_UPLOAD_SAVED = "academia.upload.saved"
ACADEMIA_UPLOAD_REJECTED = "academia.upload.rejected"
ACADEMIA_ROUTE_DECIDED = "academia.route.decided"
ACADEMIA_WEIGHTED_SCORE = "academia.weighted_score.computed"
ACADEMIA_FRAMES_EXTRACTED = "academia.frames.extracted"
ACADEMIA_ANALYZE_COMPLETED = "academia.analyze.completed"
ACADEMIA_ANALYZE_FAILED = "academia.analyze.failed"
ACADEMIA_PERSISTED = "academia.persisted"
ACADEMIA_ANALYSIS_RETRIEVED = "academia.analysis.retrieved"
ACADEMIA_ANALYSIS_EXPORTED = "academia.analysis.exported"
ACADEMIA_WARNING = "academia.warning"

# ---- gemini -------------------------------------------------------------- #
GEMINI_UPLOAD_STARTED = "gemini.upload.started"
GEMINI_UPLOAD_ACTIVE = "gemini.upload.active"
GEMINI_UPLOAD_FAILED = "gemini.upload.failed"
GEMINI_ANALYZE_STARTED = "gemini.analyze.started"
GEMINI_ANALYZE_COMPLETED = "gemini.analyze.completed"
GEMINI_NARRATE_STARTED = "gemini.narrate.started"
GEMINI_NARRATE_COMPLETED = "gemini.narrate.completed"
GEMINI_TTS_STARTED = "gemini.tts.started"
GEMINI_TTS_COMPLETED = "gemini.tts.completed"
GEMINI_TTS_RETRY = "gemini.tts.retry"
GEMINI_TTS_FAILED = "gemini.tts.failed"
GEMINI_FILE_DELETED = "gemini.file.deleted"
GEMINI_CALL_FAILED = "gemini.call.failed"

# ---- soccer (legado) ----------------------------------------------------- #
SOCCER_ANALISE_CREATED = "soccer.analise.created"
SOCCER_ANALISE_LISTED = "soccer.analise.listed"
SOCCER_ANALISE_FETCHED = "soccer.analise.fetched"
SOCCER_ANALISE_FAILED = "soccer.analise.failed"


# nome -> (categoria, descrição). Fonte da verdade do catálogo.
CATALOG: dict[str, tuple[str, str]] = {
    APP_STARTUP: (Category.SYSTEM, "Aplicação iniciada (lifespan startup)."),
    APP_SHUTDOWN: (Category.SYSTEM, "Aplicação encerrada (lifespan shutdown)."),
    BUS_STARTED: (Category.SYSTEM, "Worker do event bus iniciado."),
    BUS_STOPPED: (Category.SYSTEM, "Worker do event bus parado (flush final)."),
    EVENTS_DROPPED: (Category.SYSTEM, "Eventos descartados por fila cheia."),
    EVENTS_FLUSH_FAILED: (Category.SYSTEM, "Falha ao gravar lote de eventos no DB (mantidos no stdout)."),
    EVENTS_CLEANUP: (Category.SYSTEM, "Limpeza de eventos antigos por retenção."),
    SCHEMA_INIT: (Category.SYSTEM, "Schema do banco inicializado."),

    HTTP_REQUEST: (Category.HTTP, "Requisição HTTP concluída (método, rota, status, duração)."),
    HTTP_REQUEST_FAILED: (Category.HTTP, "Requisição HTTP com erro 5xx ou exceção não tratada."),

    TENNIS_ANALYZE_RECEIVED: (Category.TENNIS, "Pedido de análise de tênis recebido."),
    TENNIS_UPLOAD_SAVED: (Category.TENNIS, "Vídeo salvo em disco temporário (tamanho validado)."),
    TENNIS_UPLOAD_REJECTED: (Category.TENNIS, "Upload rejeitado (vazio ou acima do limite)."),
    TENNIS_ROUTE_DECIDED: (Category.TENNIS, "Roteamento decidido (gênero × modo, fps, media_resolution)."),
    TENNIS_WEIGHTED_SCORE: (Category.TENNIS, "Score ponderado calculado (match; e clip condicionado à fase)."),
    TENNIS_ANALYZE_COMPLETED: (Category.TENNIS, "Análise de tênis concluída (3 saídas)."),
    TENNIS_ANALYZE_FAILED: (Category.TENNIS, "Análise de tênis falhou."),
    TENNIS_PERSISTED: (Category.TENNIS, "Análise de tênis persistida no Postgres."),
    TENNIS_ANALYSIS_RETRIEVED: (Category.TENNIS, "Análise de tênis recuperada do histórico (lista ou por id)."),
    TENNIS_ANALYSIS_EXPORTED: (Category.TENNIS, "Análise de tênis exportada (txt/json/áudio)."),
    TENNIS_WARNING: (Category.TENNIS, "Aviso durante a análise (narrativa/áudio/persistência indisponível)."),

    ACADEMIA_ANALYZE_RECEIVED: (Category.ACADEMIA, "Pedido de análise de academia recebido."),
    ACADEMIA_UPLOAD_SAVED: (Category.ACADEMIA, "Vídeo salvo em disco temporário (tamanho validado)."),
    ACADEMIA_UPLOAD_REJECTED: (Category.ACADEMIA, "Upload rejeitado (vazio ou acima do limite)."),
    ACADEMIA_ROUTE_DECIDED: (Category.ACADEMIA, "Roteamento decidido (fps, media_resolution, duração)."),
    ACADEMIA_WEIGHTED_SCORE: (Category.ACADEMIA, "Nota de execução 0..100 calculada em Python (scoring.py) a partir do checklist."),
    ACADEMIA_FRAMES_EXTRACTED: (Category.ACADEMIA, "Prints (ffmpeg) do momento exato dos erros extraídos do vídeo."),
    ACADEMIA_ANALYZE_COMPLETED: (Category.ACADEMIA, "Análise de academia concluída (3 saídas)."),
    ACADEMIA_ANALYZE_FAILED: (Category.ACADEMIA, "Análise de academia falhou."),
    ACADEMIA_PERSISTED: (Category.ACADEMIA, "Análise de academia persistida no Postgres."),
    ACADEMIA_ANALYSIS_RETRIEVED: (Category.ACADEMIA, "Análise de academia recuperada do histórico (lista ou por id)."),
    ACADEMIA_ANALYSIS_EXPORTED: (Category.ACADEMIA, "Análise de academia exportada (txt/json/áudio)."),
    ACADEMIA_WARNING: (Category.ACADEMIA, "Aviso durante a análise (narrativa/áudio/persistência indisponível)."),

    GEMINI_UPLOAD_STARTED: (Category.GEMINI, "Upload do vídeo para a Files API iniciado."),
    GEMINI_UPLOAD_ACTIVE: (Category.GEMINI, "Vídeo ficou ACTIVE na Files API."),
    GEMINI_UPLOAD_FAILED: (Category.GEMINI, "Falha no upload/processamento do vídeo."),
    GEMINI_ANALYZE_STARTED: (Category.GEMINI, "Chamada 1 (vídeo → JSON) iniciada."),
    GEMINI_ANALYZE_COMPLETED: (Category.GEMINI, "Chamada 1 concluída."),
    GEMINI_NARRATE_STARTED: (Category.GEMINI, "Chamada 2 (JSON → narrativa) iniciada."),
    GEMINI_NARRATE_COMPLETED: (Category.GEMINI, "Chamada 2 concluída."),
    GEMINI_TTS_STARTED: (Category.GEMINI, "Chamada 3 (narrativa → áudio) iniciada."),
    GEMINI_TTS_COMPLETED: (Category.GEMINI, "Chamada 3 concluída (WAV gerado)."),
    GEMINI_TTS_RETRY: (Category.GEMINI, "Retry de TTS (erro 500 ou áudio vazio)."),
    GEMINI_TTS_FAILED: (Category.GEMINI, "TTS falhou após as tentativas."),
    GEMINI_FILE_DELETED: (Category.GEMINI, "Arquivo remoto removido da Files API."),
    GEMINI_CALL_FAILED: (Category.GEMINI, "Chamada ao Gemini falhou."),

    SOCCER_ANALISE_CREATED: (Category.SOCCER, "Análise de futebol criada (/analises)."),
    SOCCER_ANALISE_LISTED: (Category.SOCCER, "Listagem de análises de futebol."),
    SOCCER_ANALISE_FETCHED: (Category.SOCCER, "Análise de futebol obtida por id."),
    SOCCER_ANALISE_FAILED: (Category.SOCCER, "Operação de análise de futebol falhou (DB)."),
}


def category_for(name: str) -> str:
    """Categoria do evento — do catálogo, ou inferida do prefixo do nome."""
    if name in CATALOG:
        return CATALOG[name][0]
    return name.split(".", 1)[0] if "." in name else Category.SYSTEM
