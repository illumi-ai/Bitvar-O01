# Bitvar O01 — Runbook de Deploy (Docker + Traefik + Postgres)

Infraestrutura para subir a API do Bitvar O01 numa VPS Debian 12, com Traefik v3
(reverse proxy + HTTPS automático via Let's Encrypt) e Postgres 16.

## Topologia

```
Internet ──:80/:443──> Traefik (proxy + TLS)
                          │  rede bitvar_web
                          ▼
                        api (FastAPI / uvicorn :8000)
                          │  rede bitvar_internal (internal: true)
                          ▼
                        db (Postgres 16 — sem porta publicada)
```

- **bitvar_web**: rede pública entre Traefik e a API.
- **bitvar_internal**: rede privada (`internal: true`) entre API e Postgres. O
  Postgres **não** publica porta e não está acessível pela internet.

## Pré-requisitos

- VPS Debian 12 (bookworm), acesso root.
- Portas 80 e 443 livres e acessíveis externamente (ACME HTTP-01 usa a 80).
- Um domínio com registro **A** apontando para o IP da VPS:
  - `${DOMAIN}` → IP da VPS
  - `traefik.${DOMAIN}` → IP da VPS (dashboard)

## Ordem de bring-up

```bash
# 1) Instalar Docker (uma vez)
./infra/scripts/install-docker.sh

# 2) Configurar segredos
cp .env.example .env
# editar DOMAIN, POSTGRES_PASSWORD, ACME_EMAIL (ACME_CASERVER já em staging)
chmod 600 .env

# 3) Gerar o hash do dashboard e colar em infra/traefik/dynamic.yml
apt-get install -y apache2-utils
./infra/scripts/gen-dashboard-auth.sh admin 'senha-dashboard'
#   -> cole a saída na linha `users:` de dynamic.yml (use $ simples no arquivo)

# 4) DNS: A record de ${DOMAIN} e traefik.${DOMAIN} -> IP do VPS

# 5) Subir (build + start). A ordem é garantida pelo compose:
docker compose up -d --build
#    db sobe e fica (healthy) via pg_isready
#    api só inicia depois (condition: service_healthy)
#    traefik descobre a api via labels e emite o cert no primeiro acesso HTTPS

# 6) Acompanhar
docker compose ps
docker compose logs -f traefik   # erros de ACME aparecem aqui
docker compose logs -f api
```

## Testar ANTES do DNS propagar (staging, sem confiança de browser)

```bash
# liveness via Host header, ignorando cert de staging (-k):
curl -k -H "Host: ${DOMAIN}" https://<IP_DO_VPS>/health
# esperado: {"status":"ok"}

# análise de ponta a ponta:
curl -k -H "Host: ${DOMAIN}" -X POST https://<IP_DO_VPS>/analises \
  -H "Content-Type: application/json" \
  -d '{"time_casa":"Time A","time_fora":"Time B","gols_casa":2,"gols_fora":1}'
# esperado 201 com descricao_acessivel:
# "Time A marcou 2 gols e Time B marcou 1 gol. Time A venceu a partida. No total, foram marcados 3 gols."

# direto no container (sem passar pelo Traefik):
docker compose exec api python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/ready').read())"
```

## Trocar ACME staging -> prod

```bash
# 1) No .env: ACME_CASERVER=https://acme-v02.api.letsencrypt.org/directory
# 2) Apagar os certs de staging (senão o browser segue desconfiando):
docker compose down
docker volume rm bitvar_traefik_acme
docker compose up -d
# 3) Primeiro acesso HTTPS reemite o cert de produção (confiável).
```

## Backup do Postgres

```bash
docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > backup_$(date +%F).sql.gz
# restore:
gunzip -c backup_AAAA-MM-DD.sql.gz | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

## Firewall (ufw) — liberar SSH ANTES de habilitar

```bash
apt-get install -y ufw
ufw allow 22/tcp
ufw allow 80/tcp      # ACME HTTP-01 + redirect
ufw allow 443/tcp     # HTTPS
ufw --force enable
# Postgres não publica porta; confirme: ss -tlnp | grep 5432  (deve ser vazio)
```

## Endpoints da API

| Método | Path | Descrição |
|---|---|---|
| `GET` | `/` | Info do serviço + link `/docs`. |
| `GET` | `/health` | Liveness (não toca no DB). Usado pelo healthcheck. |
| `GET` | `/ready` | Readiness (`SELECT 1` no DB). `503` se o DB cair. |
| `POST` | `/analises` | Analisa uma partida e persiste. `201` com `descricao_acessivel`. |
| `GET` | `/analises?limit&offset` | Lista análises (mais recentes primeiro). |
| `GET` | `/analises/{id}` | Uma análise. `404` se não existir. |

## Falhas comuns

- **404 do Traefik**: rótulo de middleware sem sufixo `@file`, ou DNS não
  aponta para a VPS. Veja `docker compose logs traefik`.
- **502/504 intermitente**: faltou `traefik.docker.network=bitvar_web` (a API
  está em duas redes). Já configurado no compose.
- **Cert não emitido**: porta 80 bloqueada externamente, ou rate-limit do
  Let's Encrypt prod — valide em staging primeiro.
- **API responde 503 em `/analises`**: Postgres ainda não está pronto ou caiu;
  `/ready` confirma. O `depends_on: service_healthy` evita isso no boot.
