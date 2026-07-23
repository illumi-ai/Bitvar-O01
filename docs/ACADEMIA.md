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
  → harmonização + nota 0..100 (determinístico)       app/academia/scoring.py (Python, sem VLM)
  → chamada 2: JSON (+nota) → narrativa PT-BR         app/academia/gemini.py:narrate
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
  carrega o **par obrigatório** `descricao` (**o que está errado**) → `correcao`
  (**o que consertar** — instrução acionável específica daquele erro), mais
  `timestamp_s` e `gravidade` (`leve` | `moderada` | `risco_lesao`). Todo erro
  apontado vem com o seu conserto colado; a UI renderiza ❌ errado / ✅ corrigir e o
  export `.txt` sai como "O QUE ESTÁ ERRADO → COMO CONSERTAR".
- **Veredito BINÁRIO (23jul2026):** `adequada` ou `inadequada` — não existe
  "parcialmente adequada". Qualquer erro registrado em `erros` torna a execução
  `inadequada` (a gravidade modula a nota e a urgência, não o veredito); polimento
  opcional não é erro e vai como `ajuste_leve` no checklist. Imposto em código por
  `scoring.harmonize_analysis`.
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

### Parâmetros reintroduzidos (do snapshot original a368d14)

Além do núcleo acima, a chamada 1 devolve os parâmetros que existiam no módulo
original da VPS e haviam sido descartados na versão calibrada:

- **`checklist`** — a varredura de RF-002 vira parâmetro visível: exatamente UMA
  entrada por categoria (sempre as 7), cada uma com `status`
  (`adequado` | `ajuste_leve` | `a_corrigir` | `nao_observavel`), `nota_0a10`
  (rubrica no prompt; `null` quando não observável) e `observacao` (evidência).
  `ajuste_leve` é refinamento, NÃO erro (RF-004); categoria com erro em `erros`
  fica obrigatoriamente `a_corrigir`.
- **`repeticoes`** — repetições segmentadas (`indice`, `completa`,
  `inicio_s`/`transicao_s`/`fim_s` aproximados ou `null`, `observacao`), mais
  `consistencia_amplitude`/`consistencia_ritmo` e `observacao_movimento`.
- **Condições de captura** — `corpo_inteiro_visivel`, `camera_estavel`,
  `iluminacao_adequada` (tri-state) e `recomendacoes_gravacao` (como filmar
  melhor da próxima vez; vazia quando a captura está boa).

### Nota de execução determinística (`scoring.py`)

Agora **há** score em Python (o evento `academia.weighted_score.computed` deixou de
ser reservado). `scoring.harmonize_analysis` primeiro impõe em código as regras que
antes só existiam no prompt (RF-003; checklist↔erros; veredito binário: qualquer
erro registrado ⇒ "inadequada") — cada ajuste vira um `warning` visível. Depois
`scoring.compute_nota_execucao` agrega o checklist em `nota_execucao` (0–100):
notas 0..10 normalizadas, categorias `nao_observavel` fora do cálculo com pesos
renormalizados (contribuições somam a nota), fallback por status quando a nota
falta (`adequado`=0.85 · `ajuste_leve`=0.65 · `a_corrigir`=0.40), erro na categoria
limita o valor (risco_lesao **zera**), e gates/tetos: qualidade "ruim" ou <3
categorias observáveis **bloqueiam** a nota (`nota=null, valida=false`); risco de
lesão ⇒ ≤39; `inadequada` ⇒ ≤49 — a nota nunca
contradiz o veredito. **Recalibrar a nota = editar `PESOS`** em `scoring.py`.
É um indicador observacional de POC (pesos não calibrados contra gabarito).

O exercício, o equipamento e a variação são **identificados automaticamente** pela
chamada 1, sem seleção manual.

### Prints do momento do erro (`frames.py`)

Quando um erro tem `timestamp_s`, o service extrai com **ffmpeg** um JPEG do
instante exato (seek frame-accurate, reescalado a ≤640px) enquanto o vídeo
temporário ainda existe — **só para erros**; execução limpa não gera frame. Vai
na resposta como `frames_erros[]` (`{erro_index, categoria, timestamp_s,
image_base64, mime}`), a UI mostra o print dentro do card do erro (clique
amplia). Falha de extração vira `warning`; nada é persistido. Knobs:
`ACADEMIA_FRAMES_ENABLED` (default true), `ACADEMIA_FRAMES_MAX` (6),
`ACADEMIA_FRAME_MAX_WIDTH` (640), `ACADEMIA_FRAME_TIMEOUT_S` (20).

## Endpoints (`router.py`, prefixo `/academia`)

- `GET  /academia/` — dashboard (renderiza o JSON; sem nova chamada de IA).
- `GET  /academia/health` — `configured`, modelos, voz e limites (nunca a chave).
- `POST /academia/analyze` — `multipart/form-data`:
  - `file` (vídeo), `student_name` (opcional, personaliza a narrativa),
    `duration_seconds` (opcional), `with_audio` (bool, default `true`),
    `persist` (opcional; default = config).
  - Resposta `AcademiaAnalysisResponse`:
    `{ exercicio, metrics, nota_execucao, frames_erros, narrative, audio_base64, warnings, persisted_id }`.
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
`weighted_score.computed` (nota 0..100 + cobertura + ajustes de consistência) ·
`frames.extracted` (prints do momento dos erros) ·
`analyze.completed` · `analyze.failed` · `persisted` · `analysis.retrieved` ·
`analysis.exported` · `warning`. As três chamadas ao Gemini reusam os eventos
`gemini.*` compartilhados.

## Diferenças em relação ao tênis

| | Tênis | Academia |
|---|-------|----------|
| Roteamento | gênero × modo × duração | **nenhum** (pipeline único) |
| Schemas | 4 (2 formatos × 2 gêneros) | **1** (`AcademiaAnalysis`) |
| Score em Python | `weights.py` (0–100) | **`scoring.py`** (0–100, gates + tetos de coerência) |
| fps / resolução | 4/1 · high/medium | **24 · medium** (fixos) |
| Persistência | opt-out (`TENNIS_PERSIST`) | **opt-in** (`ACADEMIA_PERSIST`) |
| Saída-núcleo | score + benchmarks | **veredito + risco_lesao + 7 categorias (erros + checklist + nota)** |

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
frontend, o histórico/exportação com DB ausente, as regras de prompt (7 categorias,
checklist/repetições/captura, RN-01 erro-antes-de-elogio, disclaimers RN-03/RN-05),
a nota determinística (renormalização, fallback, gates, tetos de coerência) e a
harmonização (RF-003 em código, checklist↔erros, caminho legado sem checklist).
Rodam sem rede (Gemini mockado):

```bash
DATABASE_URL="postgresql://x:x@localhost:5432/x" GEMINI_API_KEY="test-key" \
  pytest tests/test_academia.py -q
```
