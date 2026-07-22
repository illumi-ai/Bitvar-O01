# BitVar IA · Academia — guia de implementação

Análise técnica de exercícios de **musculação** por vídeo. Espelha a arquitetura do
módulo de tênis (`app/tennis/`), mas com um recorte próprio: **um único pipeline**
(sem roteamento por gênero/modo/duração), **um único schema** (`AcademiaAnalysis`) e
**três saídas** — métricas estruturadas, narrativa PT-BR de treinador e áudio TTS.

> **Relatório educacional (RN-05):** não substitui avaliação presencial, não mede
> carga/esforço/ativação muscular e não promete hipertrofia/força/emagrecimento
> (RN-03). O vídeo é temporário e nunca é salvo; a persistência (opt-in) guarda só
> relatório/JSON/áudio.

Prompts calibrados com o Caio (17–22/07/2026) sobre um dataset de 11 vídeos
(`videos-calibragem-academia/ANALISES.md`).

## Pipeline

Orquestrado por `app/academia/service.py:AcademiaService.analyze_upload`. Diferente
do tênis, **não há roteamento**: todo vídeo segue o mesmo `fps`/`media_resolution`
fixos de `config.py`. O único gate é o teto de duração (`academia_clip_max_seconds`,
180 s) — acima disso, 413.

```
upload (multipart, em chunks p/ disco)               app/academia/service.py:_save_upload
  → teto de duração (probe via ffprobe/parser mp4)   app.tennis.routing.probe_duration_seconds (reuso)
  → chamada 1: vídeo → JSON estruturado              app/academia/gemini.py:analyze
       gemini-3.1-pro-preview · thinking high · fps 24 · MEDIA_RESOLUTION_MEDIUM
  → chamada 2: JSON → narrativa PT-BR                 app/academia/gemini.py:narrate
       gemini-3.1-pro-preview · thinking high
  → chamada 3: narrativa → áudio WAV                  app/academia/gemini.py:synthesize
       gemini-3.1-flash-tts-preview · voz Vindemiatrix · retry · PCM→WAV 24kHz mono
```

As chamadas de rede são **síncronas** (SDK `google-genai` 2.8.0) e rodam em
threadpool via `run_in_threadpool`; o contexto de correlação é re-fixado dentro do
worker para os eventos do Gemini permanecerem correlacionados à requisição — mesmo
padrão do tênis. Os helpers puros de áudio/Files-API (`_pcm_to_wav`, `_split_for_tts`,
`_extract_audio`, `_state`, …) são **reimportados de `app.tennis.gemini`**, não
duplicados. O arquivo temporário **e** o arquivo remoto da Files API são apagados
após a análise.

## Schema e calibragem (`models.py` / `prompts.py`)

`AcademiaAnalysis` é o `response_schema` da chamada 1 (nomes de campo travados). O
núcleo é `veredito` × `risco_lesao`, e a calibragem vive no system prompt:

- **RF-002 — as 7 categorias de erro são verificadas EXPLICITAMENTE e em ordem**
  (`CategoriaErro`): `amplitude`, `escapula_ombros`, `tronco`, `cervical`,
  `cotovelos`, `joelhos`, `ritmo` (+ `outro` como válvula honesta). Cada `ErroTecnico`
  tem `descricao` (linguagem de treinador), `timestamp_s` e `gravidade`
  (`leve` | `moderada` | `risco_lesao`).
- **RF-003 — regra dura de veredito:** valgo dinâmico severo, pés mal posicionados
  numa base de carga ou qualquer erro `gravidade="risco_lesao"` forçam
  `veredito="inadequada"` **e** `risco_lesao=True`, independente de quantos acertos
  existam.
- **RF-004 — anti-nitpicking:** execução correta não ganha erro inventado — `erros`
  pode (e deve) vir `[]`.
- **RN-01 — erro antes de elogio:** quando há erro relevante (sobretudo com risco de
  lesão), a narrativa NUNCA abre com elogios; o erro dominante vem primeiro, com
  orientação de interromper/corrigir. É o caso central da calibragem (vídeo 00000613,
  corrigido pelo Caio no áudio 614).
- **RN-02 — restrito ao observável:** `qualidade_video`, `angulo_camera` e
  `partes_ocultas` existem para que `confiabilidade` reflita as limitações reais do
  vídeo, não uma certeza que o modelo não tem. O veredito não é nota de execução.
- **RN-03/RNF-003 — sem métricas que o produto não mede:** o schema não tem (e não
  deve ganhar) campos de carga/esforço/ativação muscular ou hipertrofia/força/
  emagrecimento. `musculos_esperados` é informativo, não medição de ativação.

Não há score numérico calculado em Python (ao contrário do `weights.py` do tênis): o
evento `academia.weighted_score.computed` existe no catálogo mas fica **reservado/não
usado** neste MVP. O exercício, o equipamento e a variação são **identificados
automaticamente** pela chamada 1, sem seleção manual.

## Endpoints (`router.py`, prefixo `/academia`)

- `GET  /academia/` — dashboard (renderiza o JSON; sem nova chamada de IA).
- `GET  /academia/health` — `configured`, modelos, voz e limites (nunca a chave).
- `POST /academia/analyze` — `multipart/form-data`:
  - `file` (vídeo), `student_name` (opcional, personaliza a narrativa),
    `duration_seconds` (opcional), `with_audio` (bool, default `true`),
    `persist` (opcional; default = config).
  - Resposta `AcademiaAnalysisResponse`:
    `{ exercicio, metrics, narrative, audio_base64, warnings, persisted_id }`.
- `GET  /academia/analyses` — histórico paginado (vazio se persistência off / DB fora).
- `GET  /academia/analyses/{id}` — análise completa (métricas + narrativa); 404 se ausente.
- `GET  /academia/analyses/{id}/audio` — WAV da narrativa salva.
- `GET  /academia/analyses/{id}/export?format=txt|json` — relatório para baixar/compartilhar
  (o `txt` é pronto para WhatsApp).

Narrativa, áudio e persistência são **não-fatais**: falham para `warnings`, nunca
derrubam a análise.

## Upload e limites

- `academia_max_upload_mb` (default 600): o `Content-Length` é checado **antes** de
  ler o corpo (413) e o tamanho exato é revalidado ao gravar em disco, em chunks.
- Teto de duração 180 s: rejeita (413) quando a duração é conhecida e excede o limite;
  duração indeterminável **segue** (degradação graciosa, como no tênis).
- `ffmpeg`/`ffprobe` na imagem Docker validam a duração server-side antes de qualquer
  envio ao Gemini (fallback: parser mp4 do tênis).

## Configuração (`config.py`)

Independente de `app/settings.py`: tudo tem default sensato e `GEMINI_API_KEY` é
**opcional** — a app sobe sem a chave e os endpoints respondem 503 até ela existir. A
chave é **compartilhada** com o tênis (mesma env var, sem prefixo). Os demais
parâmetros usam prefixo `ACADEMIA_` no nome do campo para não colidir com os
equivalentes de tênis:

| Env                              | Default                       | O quê |
|----------------------------------|-------------------------------|-------|
| `ACADEMIA_ANALYSIS_MODEL`        | `gemini-3.1-pro-preview`      | chamadas 1 e 2 |
| `ACADEMIA_TTS_MODEL` / `_VOICE`  | `gemini-3.1-flash-tts-preview` / `Vindemiatrix` | chamada 3 |
| `ACADEMIA_ANALYSIS_THINKING_LEVEL` / `_NARRATIVE_THINKING_LEVEL` | `high` / `high` | raciocínio |
| `ACADEMIA_CLIP_MAX_SECONDS`      | `180`                         | teto de duração |
| `ACADEMIA_FPS`                   | `24`                          | eixo temporal (fases do movimento) |
| `ACADEMIA_MEDIA_RESOLUTION`      | `MEDIA_RESOLUTION_MEDIUM`     | custo × detalhe articular |
| `ACADEMIA_MAX_UPLOAD_MB`         | `600`                         | limite de upload |
| `ACADEMIA_PERSIST`               | `false`                       | persistência (**opt-in**) |

## Persistência e degradação graciosa (`store.py`)

Persistência é **opt-in** (`ACADEMIA_PERSIST`, default `false`) — o inverso do tênis
(opt-out). Reusa o pool de `app.db` (não abre conexão própria); a tabela
`academia_analyses` é criada no lifespan (`init_schema`). Tudo tolerante a DB ausente:
sem pool, escrita/leitura viram no-op (`None` / lista vazia). O WAV fica em coluna
`BYTEA` separada do `result_json` (`JSONB`) para não inflar a listagem.

## Eventos (`app/events/catalog.py`, categoria `academia`)

`academia.analyze.received` · `upload.saved` · `upload.rejected` · `route.decided` ·
`analyze.completed` · `analyze.failed` · `persisted` · `analysis.retrieved` ·
`analysis.exported` · `warning`. As três chamadas ao Gemini reusam os eventos
`gemini.*` compartilhados. `academia.weighted_score.computed` está reservado (não
emitido no MVP).

## Diferenças em relação ao tênis

| | Tênis | Academia |
|---|-------|----------|
| Roteamento | gênero × modo × duração | **nenhum** (pipeline único) |
| Schemas | 4 (2 formatos × 2 gêneros) | **1** (`AcademiaAnalysis`) |
| Score em Python | `weights.py` (0–100) | **não há** (evento reservado) |
| fps / resolução | 4/1 · high/medium | **24 · medium** (fixos) |
| Persistência | opt-out (`TENNIS_PERSIST`) | **opt-in** (`ACADEMIA_PERSIST`) |
| Saída-núcleo | score + benchmarks | **veredito + risco_lesao + 7 categorias de erro** |

## Ressalvas

- Análise **síncrona** (segura a conexão por minutos) — daí `--timeout-keep-alive 600`
  no uvicorn e `respondingTimeouts` no Traefik. Produção pede job assíncrono + polling.
- Prompts calibrados sobre 11 vídeos; ainda não validados contra gabarito amplo. O
  veredito é honesto sobre incerteza (RN-02), mas trate como orientação, não laudo.
- O benchmark biomecânico é o padrão-ouro consolidado de cada exercício embutido no
  prompt — não há tabela de referência externa.

## Testes

`tests/test_academia.py` cobre a guarda de 413 (header e streaming), o saneamento do
nome de arquivo, os três ramos de veredito (execução limpa → 3 saídas; com erros →
`erros`+veredito; `risco_lesao` → força `inadequada`), o 503 sem chave, `/health`, o
frontend, o histórico/exportação com DB ausente e as regras de prompt (7 categorias,
RN-01 erro-antes-de-elogio, disclaimers RN-03/RN-05). Rodam sem rede (Gemini mockado):

```bash
DATABASE_URL="postgresql://x:x@localhost:5432/x" GEMINI_API_KEY="test-key" \
  pytest tests/test_academia.py -q
```
