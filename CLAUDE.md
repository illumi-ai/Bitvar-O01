# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Bitvar O01 — "Módulo de IA de análise esportiva acessível". The repo holds **two
generations of functionality** that share one FastAPI process:

1. **Legacy soccer core** (`bitvar/`) — a pure-Python, dependency-free library that
   turns a match dict into stats + an accessibility-first plain-text description.
   Despite the "IA" branding, `bitvar/analise.py` is **deterministic arithmetic, not
   an LLM** — it's explicitly the extension point where a model would later plug in.
2. **Tennis video analysis** (`app/tennis/`) — the real AI feature: upload a tennis
   video → Gemini turns it into structured metrics, a PT-BR coach narrative, and TTS
   audio. This is where almost all the complexity lives. Implements
   `docs/bitvar-ia-tenis-blueprint.html`; see `docs/TENNIS.md` for the deep dive.

A third concern, **events** (`app/events/`), is an audit + observability layer that
spans both.

## Architecture

`app/main.py` is the composition root: it builds the FastAPI app, wires the legacy
soccer endpoints (`/analises`), mounts the tennis and events routers, installs the
event-capture middleware, and manages the DB pool + event-bus lifecycle in `lifespan`.

### Tennis pipeline (`app/tennis/`) — the core feature

Five stages, orchestrated by `service.py:TennisService.analyze_upload`:

```
upload (multipart, chunked to disk)        service.py:_save_upload
  → route: gender × mode (≷75s duration)    routing.py:build_route
  → Gemini call 1: video → structured JSON   gemini.py:analyze   (gemini-3.5-flash, thinking high)
  → weighted score (deterministic, Python)   weights.py:compute_weighted_score  (match mode only)
  → Gemini call 2: JSON → PT-BR narrative    gemini.py:narrate   (gemini-3.5-flash, thinking medium)
  → Gemini call 3: narrative → WAV audio     gemini.py:synthesize (gemini-3.1-flash-tts-preview, voice Vindemiatrix)
```

Returns one `TennisAnalysisResponse` (`models.py`) with `{route, metrics, benchmarks,
narrative, audio_base64, warnings, persisted_id}`. Key structural facts:

- **Routing** picks mode by duration (`<75s` → `clip`, else `match`), falling back to a
  file-size heuristic when duration is unknown (no `ffprobe`, no `mvhd` box). `clip` uses
  fps 4 / `MEDIA_RESOLUTION_HIGH`; `match` uses fps 1 / `MEDIA_RESOLUTION_MEDIUM`. A
  `Route` carries the pydantic schema model, the weight-model name, and the system prompt.
- **The "4 schemas" are 2 formats × 2 genders**: `ClipAnalysis` / `MatchAnalysis`
  (`models.py`) crossed with male/female (which swaps `gender_profile`,
  `benchmark_reference`, and the weight model). Gender is a routing parameter, not a
  schema branch — this deliberately avoids one mega-schema with `anyOf`.
- **`weighted_performance_score` is computed in Python, never by the VLM** (the model is
  bad at arithmetic). `weights.py` normalizes each present component to 0..1, applies the
  weight, and **re-normalizes over present components** so contributions always sum to the
  score (0–100). **Recalibrating = editing `WEIGHT_MODELS`** in `weights.py`.
- Network calls are **synchronous (the `google-genai` SDK is sync)** and run in a
  threadpool via `run_in_threadpool`; `service.py` re-sets the event correlation context
  inside the worker thread so Gemini events stay correlated to the request.
- The temp upload file **and** the remote Files-API file are deleted after analysis.

### Events system (`app/events/`) — audit + observability

`emit(name, ...)` (see `__init__.py`) is the only entry point. It enriches each event
with the request's `correlation_id`/`actor` (from `context.py` contextvars), then hands it
to the singleton `bus`. The bus (`bus.py`) has two independent sinks:

- **stdout** (synchronous, durable backup) — one JSON line per event.
- **DB** (`events` table, best-effort) — fed through a thread-safe `queue.Queue`, drained
  by an asyncio worker that batches writes via `asyncio.to_thread` (psycopg is sync). Full
  queue → drop + count (data already hit stdout).

`emit()` and `bus.emit()` **never raise** — auditing must never take down a request.
`catalog.py` is the single source of truth for every possible event name (`category.object.action`),
exposed at `GET /events/catalog`, so the tracked-event set is enumerable. `middleware.py`
auto-captures HTTP requests; query/inspect via the `/events*` routes and `/events/ui`.

### Persistence & the graceful-degradation model

`app/db.py` is a psycopg3 pool that **opens non-blocking** (`open=False` then
`open(wait=False)`), so the app boots even if Postgres is down. Every pool call passes
`timeout=settings.db_connect_timeout` (5s) to fail fast instead of hanging ~30s.
Two independent "this dependency may be absent" stories run throughout:

- **No `GEMINI_API_KEY`** → app still boots; tennis endpoints return `503` until the key
  exists (`tennis/config.py:configured`). Tennis config is intentionally **separate** from
  `app/settings.py` so the key stays optional while `DATABASE_URL` stays required.
- **No/slow Postgres** → `/health` (liveness) ignores the DB; `/ready` reflects real state
  with `SELECT 1`. Tennis persistence is opt-in (`TENNIS_PERSIST`) and failures become
  `warnings`, never errors.

## Commands

Run from the repo root.

```bash
# Install deps (standard environment)
pip install -r requirements.txt

# Run locally
export DATABASE_URL=postgresql://user:pass@localhost:5432/bitvar   # required (read at import)
export GEMINI_API_KEY=...                                          # optional; tennis 503s without it
uvicorn app.main:app --reload --timeout-keep-alive 600
#   UIs: http://localhost:8000/tennis/   ·   /events/ui   ·   /docs

# Tests — no network and no live DB required
DATABASE_URL="postgresql://x:x@localhost:5432/x" GEMINI_API_KEY="test-key" python3 -m pytest -q

# Single file / single test
python3 -m pytest tests/test_tennis.py -q
python3 -m pytest tests/test_tennis.py::test_analyze_match_has_weighted_score -q
```

- **Why the env vars on the test command:** `app/settings.py` instantiates `Settings()` at
  import and requires `DATABASE_URL`; tennis endpoint tests need `GEMINI_API_KEY` set
  *before* import so the pipeline reads as "configured". `test_tennis.py` self-sets both via
  `os.environ.setdefault`, but passing them explicitly makes the full suite import-order-proof.
- **Tests never touch the network** (Gemini is mocked with `_FakeGemini`) or a live DB.
  `test_tennis.py` builds its `TestClient` **without** a `with` block so the lifespan never
  runs; `test_api.py` deliberately exercises the dead-DB path and spends ~5s/test on the
  connect timeout. `test_analise.py` is pure `bitvar`.
- **No linter/formatter is configured** (no ruff/black/flake8/mypy in deps or `pyproject.toml`).
- **No frontend build step** — `app/static/{tennis,events}/index.html` are served as-is.

## Deploy

The **root `docker-compose.yml` is the production deploy**: a self-contained,
label-routed stack of `traefik` + `db` (Postgres 16, private network, no published port) +
`api`. The `infra/bitvar-001/*.example.yml` file-provider variant (joining a pre-existing
shared Traefik) is **not** used here — don't confuse the two. Runbook: `infra/README.md`.

```bash
cp .env.example .env        # set DOMAIN, POSTGRES_PASSWORD, ACME_EMAIL, ACME_CASERVER, GEMINI_API_KEY
docker compose up -d --build
docker compose logs -f traefik   # ACME errors surface here
```

Non-obvious deploy gotchas (already baked into the committed config — keep them):

- **Traefik must be `v3.6`, not `v3.3`.** v3.3's Docker provider can't negotiate the API
  with a Docker 29.x daemon (falls back to API 1.24 → "client version 1.24 is too old"),
  and `DOCKER_API_VERSION` is ignored by v3.3.
- **Traefik does NOT interpolate `{{ env }}` in the static config** (`infra/traefik/traefik.yml`).
  ACME email/caServer are passed via **native env vars**
  (`TRAEFIK_CERTIFICATESRESOLVERS_LE_ACME_EMAIL` / `_CASERVER`) in compose, not templates.
- **The dashboard router is intentionally disabled** in compose (no `traefik.${DOMAIN}` DNS,
  example basic-auth hash). Re-enable only with real DNS + a real hash.
- **Switching ACME staging → prod:** clear the `bitvar_traefik_acme` volume first, or the
  browser keeps distrusting the staging cert.
- The API serves `/tennis/` from the **same container** as the rest of `app.main`.

## Dev environment notes (this VPS sandbox)

- **This working copy is the live production host.** It is deployed at
  **https://001.bitvar.illumiai.com/** (`/tennis/`) with a real Let's Encrypt cert.
  `docker compose up -d --build` here affects the live site — treat it accordingly.
- **No `pip` / `venv` / `ensurepip`** is preinstalled. Bootstrap once:
  `cd /tmp && curl -sSO https://bootstrap.pypa.io/get-pip.py && python3 get-pip.py --break-system-packages`,
  then `python3 -m pip install --break-system-packages -r requirements.txt`.
- **Postgres is not running** in the sandbox, so DB-touching code hits the 5s connect
  timeout. This is why the test invocation above works without a database.

## Reconciling the blueprint with the real SDK (`google-genai` 2.8.0)

The blueprint cites a "3.x" syntax that doesn't match the installed SDK. When editing
`app/tennis/gemini.py`, use the real names:

- `response_mime_type` + `response_schema` (a pydantic model) — **not** `response_format`.
- `MEDIA_RESOLUTION_HIGH` / `MEDIA_RESOLUTION_MEDIUM` — there is no `ultra_high`.
- Video goes through the **Files API**, which starts in `PROCESSING`; poll until `ACTIVE`
  before calling `analyze` (`gemini.py:upload_video`).
- `thinking_config` levels (`"high"`/`"medium"`), `VideoMetadata(fps=…)`, and the TTS
  `SpeechConfig`/`PrebuiltVoiceConfig` (`voice_name="Vindemiatrix"`) match the blueprint.

## Caveats to respect (from the blueprint, §09)

- **`clip` (qualitative coaching) is reliable; `match` (counting statistics) is approximate**
  and unvalidated against ground truth — prompts are written to be honest about uncertainty,
  and weights are uncalibrated (blueprint Fase 5). Don't present match stats as exact.
- Benchmarks are **tennis (ATP/WTA), not beach tennis**.
- Analysis is **synchronous** (a request can hold the connection for minutes — hence
  `--timeout-keep-alive 600` on uvicorn and `respondingTimeouts: 600s` in Traefik).
  Production-grade use wants async job + polling.
