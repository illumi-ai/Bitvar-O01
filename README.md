# Bitvar O01

Módulo de IA de análise esportiva acessível.

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
# abra http://localhost:8000/tennis/  · eventos em http://localhost:8000/events/ui
```

Pipeline (`app/tennis/`): upload → roteamento (gênero × modo por duração) →
Gemini 3.5 Flash (vídeo→JSON) → Gemini 3.5 Flash (JSON→texto PT-BR) →
Gemini 3.1 Flash TTS (texto→áudio). Endpoints: `GET /tennis/` (dashboard),
`POST /tennis/analyze`, `GET /tennis/health`. O upload aceita ainda a
**identificação do jogador** (`player_name`, `player_outfit`, `player_side`,
`player_notes`) — essencial em clipes com várias pessoas: a análise foca só nele.

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
- [ ] Validação do modo match contra gabarito + calibração dos pesos (blueprint Fase 5)
- [ ] Análise assíncrona (job_id + polling) para produção
- [ ] Integração com APIs de dados esportivos em tempo real
- [ ] Beach tennis (vocabulário de golpes e benchmarks próprios)
- [ ] Suporte a múltiplos esportes
