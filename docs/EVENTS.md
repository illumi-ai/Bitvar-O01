# Sistema de eventos — auditoria & observabilidade

Rastreia *todos os eventos possíveis* da aplicação e os grava numa tabela para
auditoria, além do stdout para observabilidade.

## Como funciona

```
emit(nome, ...)                              app/events/__init__.py
  → preenche correlation_id/actor do contexto (contextvars)   app/events/context.py
  → redige segredos (scrub / scrub_text)                       app/events/models.py
  → sink stdout (linha JSON, imediato)        ┐ dois sinks      app/events/sinks.py
  → fila thread-safe → worker async → DB       ┘ independentes   app/events/bus.py
```

- **Dois sinks**: stdout (sempre, durável p/ agregadores de log) e Postgres
  (best-effort, consultável). Com o banco fora, o stdout garante que nada se perde.
- **emit() nunca levanta** e é seguro de qualquer thread (inclusive o threadpool
  do pipeline de tênis). Fila cheia → conta e emite `system.events.dropped`.
- **Middleware ASGI** (`app/events/middleware.py`, camada mais externa) rastreia
  toda requisição: atribui/propaga `X-Request-ID` (inclusive em respostas 500),
  mede duração, emite `http.request` (pula ruído: `/health`, `/ready`, `/events`…).
- **Correlação**: todos os eventos de uma requisição compartilham o
  `correlation_id` (o `X-Request-ID`), inclusive os emitidos no threadpool.

## Tabela `events`

`id, event_id (uuid), ts, category, name, level, status, correlation_id, actor,
source, method, path, status_code, duration_ms, message, data (jsonb), error` —
com índices por ts, category, name, level, correlation_id, status_code.

## API

| Endpoint | O quê |
|---|---|
| `GET /events/ui` | Dashboard (cards, gráficos, tabela com filtros) |
| `GET /events` | Lista com filtros: `category, name, level, status, correlation_id, actor, status_code, since, until, q, limit, offset` |
| `GET /events/stats?hours=24` | Agregados (por categoria/nível, top, erros, taxa de erro HTTP, média ms) + estado do bus |
| `GET /events/catalog` | Todos os eventos possíveis (taxonomia documentada) |
| `GET /events/{correlation_id}` | Linha do tempo de uma requisição |

## Catálogo (categorias)

`system` (lifecycle, bus, drops, flush_failed, cleanup), `http` (request,
request.failed), `tennis` (received, upload.saved/rejected, route.decided,
weighted_score, completed/failed, persisted, warning), `gemini`
(upload/analyze/narrate/tts started/completed/retry/failed, file.deleted),
`soccer` (legado). Lista completa e descrições: `app/events/catalog.py` /
`GET /events/catalog`.

## Configuração (env)

`EVENTS_ENABLED`, `EVENTS_TO_DB`, `EVENTS_TO_STDOUT`, `EVENTS_STDOUT_MIN_LEVEL`,
`EVENTS_QUEUE_MAX`, `EVENTS_BATCH_MAX`, `EVENTS_FLUSH_INTERVAL_S`,
`EVENTS_RETENTION_DAYS` (limpeza periódica), `EVENTS_CAPTURE_HTTP`,
`EVENTS_SKIP_PATHS`. Defaults sensatos em `app/events/config.py`.

## Segurança

Segredos nunca vão para log/DB: `scrub()` redige chaves sensíveis em `data` e
`scrub_text()` redige credenciais embutidas em texto livre (ex.: DSN
`postgresql://user:***@…`, `api_key=***`) em `error`/`message`.
