"""Middleware ASGI puro que rastreia toda requisição HTTP.

ASGI puro (não ``BaseHTTPMiddleware``) de propósito: define os ``contextvars``
no MESMO task que chama o app downstream, então ``correlation_id`` fica visível
no endpoint e — via anyio — dentro do threadpool do pipeline. Atribui/propaga
``X-Request-ID``, mede a duração e emite ``http.request`` (e ``http.request.failed``
em 5xx/exceção). Paths de ruído (healthcheck, própria API de eventos) são pulados.
"""

from __future__ import annotations

import time
import uuid

from . import catalog, emit, set_context
from .config import EventSettings
from .config import event_settings as default_cfg


class EventMiddleware:
    def __init__(self, app, settings: EventSettings = default_cfg):
        self.app = app
        self.cfg = settings
        self._skip = settings.skip_path_list

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.cfg.events_capture_http:
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        rid = headers.get(b"x-request-id", b"").decode() or uuid.uuid4().hex
        method = scope.get("method", "")
        path = scope.get("path", "")
        actor = self._client_ip(headers, scope)
        set_context(correlation_id=rid, actor=actor, method=method, path=path)

        skipped = any(path.startswith(p) for p in self._skip)
        start = time.monotonic()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            dur = (time.monotonic() - start) * 1000
            emit(catalog.HTTP_REQUEST_FAILED, level="error", status="error", error=exc,
                 method=method, path=path, status_code=500, duration_ms=round(dur, 1),
                 actor=actor, message=f"{method} {path} levantou exceção")
            raise
        if skipped:
            return
        dur = round((time.monotonic() - start) * 1000, 1)
        code = status["code"]
        level = "error" if code >= 500 else "warning" if code >= 400 else "info"
        emit(catalog.HTTP_REQUEST, level=level, status="error" if code >= 400 else "ok",
             method=method, path=path, status_code=code, duration_ms=dur, actor=actor)
        if code >= 500:
            emit(catalog.HTTP_REQUEST_FAILED, level="error", status="error",
                 method=method, path=path, status_code=code, duration_ms=dur, actor=actor)

    @staticmethod
    def _client_ip(headers: dict, scope) -> str | None:
        xff = headers.get(b"x-forwarded-for")
        if xff:
            return xff.decode().split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else None
