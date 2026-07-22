# BitVar IA · Tênis — guia de implementação

Implementação do blueprint [`bitvar-ia-tenis-blueprint.html`](bitvar-ia-tenis-blueprint.html).
Um motor, dois jogos (M/F), dois modos (clipe/partida), **três saídas** (métricas,
texto, áudio). Sem banco obrigatório — persistência é opcional.

## Pipeline

```
upload (multipart, em chunks)                         app/tennis/service.py
  → roteamento: gênero × modo (duração ~75s)          app/tennis/routing.py
  → chamada 1: vídeo → JSON estruturado               app/tennis/gemini.py:analyze
       gemini-3.5-flash · thinking high · fps 4/1 · media_resolution high/medium
  → score ponderado (determinístico, no Python)       app/tennis/weights.py
  → chamada 2: JSON → narrativa PT-BR                  app/tennis/gemini.py:narrate
       gemini-3.5-flash · thinking medium
  → chamada 3: narrativa → áudio WAV                   app/tennis/gemini.py:synthesize
       gemini-3.1-flash-tts-preview · voz Vindemiatrix · retry · PCM→WAV 24kHz mono
```

## Roteamento (blueprint §02)

| Modo  | Detecção            | fps | media_resolution        | schema         | pesos |
|-------|---------------------|-----|-------------------------|----------------|-------|
| clip  | duração < 75 s      | 4   | `MEDIA_RESOLUTION_HIGH` | `ClipAnalysis` | —     |
| match | duração ≥ 75 s      | 1   | `MEDIA_RESOLUTION_MEDIUM`| `MatchAnalysis`| `{gender}_…_v1` |

Override manual via `mode=clip|match|auto`. Sem duração (sem `ffprobe` e sem box
`mvhd`), cai para heurística de tamanho de arquivo. Os "4 schemas" do blueprint são
2 formatos × 2 gêneros: o gênero muda `gender_profile`, `benchmark_reference` e o
modelo de pesos — evitando o mega-schema com `anyOf` que a doc desaconselha.

## Reconciliação com o SDK real (`google-genai` 2.8.0)

O blueprint cita a sintaxe "3.x"; o SDK instalado expõe nomes ligeiramente diferentes:

| Blueprint                              | SDK real usado                                   |
|----------------------------------------|--------------------------------------------------|
| `response_format.text.schema`          | `response_mime_type` + `response_schema` (Pydantic) |
| `media_resolution: high/medium`        | `MEDIA_RESOLUTION_HIGH` / `MEDIA_RESOLUTION_MEDIUM` (sem `ultra_high`) |
| `thinking_config.thinking_level`       | igual (`"high"`/`"medium"`)                      |
| `VideoMetadata(fps=…)`                 | igual                                            |
| TTS `SpeechConfig`/`PrebuiltVoiceConfig`| igual (`voice_name="Vindemiatrix"`)             |

Detalhe não citado no blueprint: vídeo via **Files API** começa em `PROCESSING` —
o cliente aguarda `ACTIVE` antes de analisar (`upload_video`).

## Score ponderado (blueprint §4.3 / §5.1)

`weighted_performance_score` **não** é pedido ao VLM (que erra aritmética); é
calculado em `app/tennis/weights.py` a partir das estatísticas brutas. Cada
componente é normalizado para 0..1, multiplicado pelo peso e somado (0-100).
Componentes sem dado são omitidos e os pesos restantes re-normalizados — então as
contribuições sempre somam o score. **Recalibrar = editar `WEIGHT_MODELS`** (Fase 5).

## Endpoints

- `GET  /tennis/` — dashboard (renderiza o JSON; sem nova chamada de IA).
- `GET  /tennis/health` — `configured`, modelos, limites (nunca a chave).
- `POST /tennis/analyze` — `multipart/form-data`:
  - `file` (vídeo), `gender` (`male|female|m|f|masculino|feminino`),
    `mode` (`auto|clip|match`), `with_audio` (bool), `duration_seconds` (opcional),
    `persist` (opcional).
  - Resposta: `{ route, metrics, benchmarks, narrative, audio_base64, warnings, persisted_id }`.

## Upload e limites (blueprint §10)

- `MAX_UPLOAD_MB` (default 600): o `Content-Length` é checado **antes** de ler o
  corpo (413) e o tamanho exato é revalidado ao gravar em disco, em chunks.
- O arquivo temporário é apagado após o envio ao Gemini (e o arquivo remoto também).
- Deploy: `--timeout-keep-alive 600` (uvicorn) e `respondingTimeouts: 600s`
  (Traefik) — análise com `thinking high` segura a conexão por minutos.

## Configuração

Tudo via env (`app/tennis/config.py`), com defaults do blueprint:
`GEMINI_API_KEY` (obrigatória p/ operar), `MAX_UPLOAD_MB`, `TENNIS_PERSIST`,
e overrides opcionais (`ANALYSIS_MODEL`, `TTS_MODEL`, `TTS_VOICE`, `CLIP_MAX_SECONDS`,
`CLIP_FPS`, `MATCH_FPS`). A app sobe **sem** a chave; os endpoints respondem 503
até ela existir.

## Deploy alternativo (Traefik existente, file provider)

Veja `infra/bitvar-001/docker-compose.example.yml` e `dynamic.example.yml`
(blueprint §10.1/§10.2) — a app entra numa stack Traefik já rodando, sem labels.
O `docker-compose.yml` da raiz continua subindo a stack completa com labels.

## Ressalvas (blueprint §09)

- **Clipe (qualitativo) é confiável; match (contar estatística) é aproximado** —
  validar contra gabarito antes de virar promessa (Fase 5). Os prompts pedem
  honestidade sobre incerteza.
- Benchmarks são de **tênis** (ATP/WTA), não beach tennis.
- Síncrono serve para validação; produção pede análise assíncrona (job + polling).

## Testes

`tests/test_tennis.py` cobre roteamento, pesos (soma 1.0, re-normalização,
penalidade de dupla falta), WAV/TTS helpers, guarda de 413 (header e streaming) e
os endpoints com o Gemini mockado (clipe com 3 saídas; match com score ponderado).
Rodam sem rede:

```bash
pytest tests/test_tennis.py -q
```
