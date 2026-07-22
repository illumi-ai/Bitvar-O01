# Bitvar O01

Módulo de IA de análise esportiva e de movimento acessível.

## Objetivo

Fornecer análises esportivas (estatísticas, tendências e resumos de partidas) com foco em **acessibilidade**: saídas em linguagem natural simples, compatíveis com leitores de tela, e descrições textuais de gráficos e dados visuais.

## Estrutura

```
Bitvar-O01/
├── bitvar/
│   ├── __init__.py
│   ├── analise.py        # Núcleo de análise esportiva (estatísticas e tendências)
│   └── acessibilidade.py # Formatação acessível das análises (texto simples, leitores de tela)
├── tests/
│   └── test_analise.py
├── requirements.txt
└── README.md
```

## Instalação

```bash
pip install -r requirements.txt
```

## Uso rápido

```python
from bitvar import AnalisadorEsportivo

analisador = AnalisadorEsportivo()
resultado = analisador.analisar_partida({
    "time_casa": "Time A",
    "time_fora": "Time B",
    "gols_casa": 2,
    "gols_fora": 1,
})
print(resultado.descricao_acessivel)
```

## Acessibilidade

- Todas as análises geram uma `descricao_acessivel` em texto simples.
- Sem dependência de elementos visuais para entender os resultados.
- Estruturado para integração futura com TTS (texto-para-voz).

## BitVar IA — Análise de Tênis por vídeo (M/F · clipe/partida)

Implementação do blueprint [`docs/bitvar-ia-tenis-blueprint.html`](docs/bitvar-ia-tenis-blueprint.html):
sobe um vídeo, escolhe o gênero e recebe **três saídas** — métricas visuais,
relatório de coach em PT-BR e o áudio da narrativa (voz Vindemiatrix). Detalhes
em [`docs/TENNIS.md`](docs/TENNIS.md).

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...                 # a chave do Gemini
export DATABASE_URL=postgresql://...      # exigida pela app (persistência é opcional)
uvicorn app.main:asgi_app --reload --timeout-keep-alive 600
# abra /tennis/ · /academia/ · eventos em /events/ui
```

Pipeline (`app/tennis/`): upload → roteamento (gênero × modo por duração) →
Gemini 3.5 Flash (vídeo→JSON) → Gemini 3.5 Flash (JSON→texto PT-BR) →
Gemini 3.1 Flash TTS (texto→áudio). Endpoints: `GET /tennis/` (dashboard),
`POST /tennis/analyze`, `GET /tennis/health`. O upload aceita ainda a
**identificação do jogador** (`player_name`, `player_outfit`, `player_side`,
`player_notes`) — essencial em clipes com várias pessoas: a análise foca só nele.

## BitVar IA — Academia (musculação com identificação automática)

Vertical isolada em [`app/academia/`](app/academia), derivada do pipeline robusto
de vídeo do tênis, mas com domínio, schemas, passes, classificação/score e
persistência próprios.
A pessoa envia o vídeo de uma série de musculação, sem selecionar o exercício. Um
primeiro passe identifica automaticamente exercício, variação e
equipamento/máquina, além de verificar se a pessoa-alvo pode ser acompanhada
quando há outras pessoas no quadro. O limite padrão é de **3 minutos**.

Para facilitar essa identificação, a página permite descrever a pessoa-alvo por
texto ou pelo microfone. O `MediaRecorder` grava até **30 segundos / 8 MB** e
envia o clipe, com consentimento explícito, a
`POST /academia/transcribe-target`. O servidor normaliza a gravação com
FFmpeg para WAV PCM mono a 16 kHz e a envia inline ao Gemini
(`gemini-3.5-flash` por padrão), sem Files API. O áudio de entrada não é
persistido: os temporários são removidos após a chamada, e somente a transcrição
revisável que a pessoa mantiver no formulário segue como dica para a análise do
vídeo. Digitar continua disponível como fallback.

A metodologia específica `squat_poc_v1` continua reservada ao agachamento.
Outras **14 famílias reconhecidas** recebem
`general_execution_observational_v1`: o Gemini amostra o intervalo ativo a
**8 fps** e o relatório observa ritmo, amplitude, trajetória, controle,
estabilidade, alinhamento, consistência e interação com o equipamento. Essa
rota geral apresenta classificação e confiabilidade/cobertura, sem nota
numérica e sem emprestar o checklist do agachamento. A família `other` retorna
`unsupported_exercise`; uma identificação inconclusiva retorna
`exercise_unknown`.

O relatório pode explicar a ênfase visual observada e os papéis musculares
normalmente esperados para a família identificada. Isso é contexto educacional:
o vídeo não mede EMG, carga, esforço ou fadiga e não permite afirmar ativação
individual, eficácia, hipertrofia, transferência de performance, fraqueza ou
diagnóstico muscular.

```text
GET  /academia/                         página de upload e relatório
GET  /academia/health                   configuração pública, sem a chave
POST /academia/transcribe-target        áudio curto → texto revisável via Gemini
POST /academia/analyze                  vídeo → JSON + texto + WAV opcional
GET  /academia/analyses                 histórico administrativo com Bearer
GET  /academia/analyses/{id}/export     relatório TXT ou JSON
DELETE /academia/analyses/{id}          exclusão administrativa protegida
```

As metodologias específica e geral são deliberadamente marcadas como **POC não
validada**. O indicador de agachamento e a classificação geral não são
diagnóstico nem medida de risco de lesão, e qualquer meta de precisão depende de
vídeos rotulados e revisão especialista. Captura inadequada ou pessoa-alvo
ambígua retorna resultado seguro sem inventar correções. O vídeo local e o
arquivo remoto são temporários e removidos ao fim do processamento. Guia
técnico, privacidade, configuração e rastreabilidade em
[`docs/ACADEMIA.md`](docs/ACADEMIA.md).

## Sistema de eventos (auditoria & observabilidade)

Toda requisição HTTP e cada etapa do pipeline/Gemini gera um evento, gravado na
tabela `events` (Postgres) e no stdout (JSON estruturado). Detalhes em
[`docs/EVENTS.md`](docs/EVENTS.md). Endpoints: `GET /events/ui` (dashboard),
`GET /events`, `GET /events/stats`, `GET /events/catalog`,
`GET /events/{correlation_id}` (linha do tempo de uma requisição). Segredos são
redigidos; a app continua funcionando com o banco fora (eventos ficam no stdout).

## Deploy (Docker + Traefik + Postgres)

A API HTTP do Bitvar (FastAPI, em `app/`) embrulha o `AnalisadorEsportivo` e
persiste as análises no Postgres, atrás do Traefik com HTTPS automático
(Let's Encrypt). Para subir a stack numa VPS Debian 12, veja o runbook completo
em [`infra/README.md`](infra/README.md):

```bash
./infra/scripts/install-docker.sh   # instala Docker + Compose v2 (idempotente)
cp .env.example .env                # configure DOMAIN, senha do Postgres, ACME
docker compose up -d --build        # sobe traefik + db + api
```

## Roadmap

- [x] Análise de tênis por vídeo com Gemini (clipe técnico + estatística de partida)
- [x] Saída em áudio (TTS) — narrativa de coach em PT-BR (voz Vindemiatrix)
- [x] Vertical de academia — identificação automática de exercício, variação e equipamento
- [x] Descrição da pessoa-alvo por microfone com transcrição temporária via Gemini
- [x] POC técnica de agachamento com captura, segmentos, checklist e relatório
- [x] Análise observacional geral para 14 famílias reconhecidas, sem score numérico
- [ ] Validar `squat_poc_v1` com especialista e vídeos ground-truth
- [ ] Adicionar metodologias próprias e validadas às famílias hoje atendidas pela análise geral
- [ ] Validação do modo match contra gabarito + calibração dos pesos (blueprint Fase 5)
- [ ] Análise assíncrona (job_id + polling) para produção
- [ ] Integração com APIs de dados esportivos em tempo real
- [ ] Beach tennis (vocabulário de golpes e benchmarks próprios)
- [ ] Suporte a múltiplos esportes
