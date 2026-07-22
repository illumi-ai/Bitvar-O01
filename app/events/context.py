"""Contexto por-requisição via ``contextvars`` para correlacionar eventos.

O middleware HTTP define ``correlation_id`` (+ ip/método/rota); os ``emit()`` de
domínio leem esses valores automaticamente, de modo que todos os eventos de uma
mesma requisição compartilham o ``correlation_id`` — inclusive os emitidos dentro
do threadpool do pipeline de tênis (o contexto é copiado para o worker thread, e
ainda assim é re-setado explicitamente lá por segurança).
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "bitvar_correlation_id", default=None
)
_actor: contextvars.ContextVar[str | None] = contextvars.ContextVar("bitvar_actor", default=None)
_method: contextvars.ContextVar[str | None] = contextvars.ContextVar("bitvar_method", default=None)
_path: contextvars.ContextVar[str | None] = contextvars.ContextVar("bitvar_path", default=None)


@dataclass
class RequestContext:
    correlation_id: str | None = None
    actor: str | None = None
    method: str | None = None
    path: str | None = None


def set_context(correlation_id=None, actor=None, method=None, path=None) -> None:
    if correlation_id is not None:
        _correlation_id.set(correlation_id)
    if actor is not None:
        _actor.set(actor)
    if method is not None:
        _method.set(method)
    if path is not None:
        _path.set(path)


def get_context() -> RequestContext:
    return RequestContext(
        correlation_id=_correlation_id.get(),
        actor=_actor.get(),
        method=_method.get(),
        path=_path.get(),
    )


def get_correlation_id() -> str | None:
    return _correlation_id.get()
