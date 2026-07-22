# ---------- build ----------
FROM python:3.11-slim-bookworm AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------- runtime ----------
FROM python:3.11-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
# ffprobe valida a duração server-side de MP4/MOV/WebM/AVI/MPEG antes de qualquer
# envio ao Gemini; o parser Python continua como fallback para MP4/MOV.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
# usuário não-root
RUN groupadd -r app && useradd -r -g app -d /app -s /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
# código depois das deps -> muda com frequência, fica na camada final
COPY bitvar/ ./bitvar/
COPY app/    ./app/
# app/static/ (frontends isolados de tênis, academia e eventos) entra no COPY acima
USER app
EXPOSE 8000
# healthcheck bate no liveness (/health), não no DB -> queda do Postgres não marca unhealthy.
# usa urllib da stdlib -> não precisa instalar curl (imagem menor, menos superfície).
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"
# Keep-alive de 600s acompanha a janela operacional do proxy. Este parâmetro
# controla conexões ociosas entre requisições; a análise síncrona continua
# protegida pelos timeouts explícitos do Traefik.
CMD ["uvicorn", "app.main:asgi_app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--timeout-keep-alive", "600"]
