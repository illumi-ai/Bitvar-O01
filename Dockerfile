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
# usuário não-root
RUN groupadd -r app && useradd -r -g app -d /app -s /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
# código depois das deps -> muda com frequência, fica na camada final
COPY bitvar/ ./bitvar/
COPY app/    ./app/
# app/static/ (frontend do tênis) já entra no COPY app/ acima
USER app
EXPOSE 8000
# healthcheck bate no liveness (/health), não no DB -> queda do Postgres não marca unhealthy.
# usa urllib da stdlib -> não precisa instalar curl (imagem menor, menos superfície).
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"
# --timeout-keep-alive 600: análise de vídeo (Gemini thinking high) segura a
# conexão aberta por minutos; sem isso o upload/anáise longos são cortados.
CMD ["uvicorn", "app.main:asgi_app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--timeout-keep-alive", "600"]
