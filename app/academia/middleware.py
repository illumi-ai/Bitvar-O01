"""Proteções ASGI aplicadas antes do parser dos uploads multimídia.

O ``UploadFile`` do FastAPI só chega ao endpoint depois que o multipart foi
recebido. Portanto, tamanho e concorrência precisam ser limitados numa camada
ASGI externa para que clientes lentos ou corpos sem ``Content-Length`` não
ocupem disco sem passar pelo semáforo do serviço.
"""

from __future__ import annotations

import asyncio

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _RequestBodyTooLarge(MultiPartException):
    """Interrompe o parser e aciona o fechamento dos arquivos parciais."""

    def __init__(self) -> None:
        super().__init__("corpo da requisição acima do limite permitido")


class VideoUploadGuard:
    """Limita corpo e uploads em voo antes da materialização do multipart."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        guarded_paths: set[str] | frozenset[str] | tuple[str, ...],
        max_body_bytes: int,
        max_concurrent_uploads: int,
        acquire_timeout_seconds: float = 2.0,
        too_large_detail: str = "corpo da requisição acima do limite permitido",
        busy_detail: str = (
            "o analisador está ocupado; tente novamente em alguns instantes."
        ),
    ) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes deve ser positivo")
        if max_concurrent_uploads <= 0:
            raise ValueError("max_concurrent_uploads deve ser positivo")
        if not guarded_paths:
            raise ValueError("guarded_paths não pode ser vazio")
        self.app = app
        self.guarded_paths = frozenset(guarded_paths)
        self.max_body_bytes = max_body_bytes
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.too_large_detail = too_large_detail
        self.busy_detail = busy_detail
        self._slots = asyncio.Semaphore(max_concurrent_uploads)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._guards(scope):
            await self.app(scope, receive, send)
            return

        declared = self._content_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            await self._reject(send, 413, self.too_large_detail)
            return

        try:
            await asyncio.wait_for(
                self._slots.acquire(), timeout=self.acquire_timeout_seconds
            )
        except TimeoutError:
            await self._reject(
                send,
                429,
                self.busy_detail,
                retry_after="5",
            )
            return

        response_started = False
        body_rejected = False
        received = 0

        async def limited_receive() -> Message:
            nonlocal body_rejected, received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    body_rejected = True
                    raise _RequestBodyTooLarge()
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            # O ServerErrorMiddleware interno tenta transformar a interrupção
            # da leitura em 500 antes de relançá-la. Esse 500 não pode escapar:
            # a camada externa responderá com o 413 correto.
            if body_rejected:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            # O multipart é consumido antes de o endpoint iniciar a resposta.
            # A salvaguarda evita tentar enviar um segundo status se um app ASGI
            # alternativo tiver começado uma resposta prematuramente.
            if response_started:
                raise
            await self._reject(
                send, 413, self.too_large_detail
            )
        else:
            # Também cobre um parser alternativo que absorva a exceção e tente
            # produzir sua própria resposta depois de ultrapassar o teto.
            if body_rejected:
                if response_started:
                    raise _RequestBodyTooLarge
                await self._reject(
                    send, 413, self.too_large_detail
                )
        finally:
            self._slots.release()

    def _guards(self, scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and scope.get("method", "").upper() == "POST"
            and scope.get("path") in self.guarded_paths
        )

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 0 else None
        return None

    @staticmethod
    async def _reject(
        send: Send,
        status_code: int,
        detail: str,
        *,
        retry_after: str | None = None,
    ) -> None:
        headers = {
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "Connection": "close",
        }
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail},
            headers=headers,
        )
        await response({"type": "http"}, receive=lambda: None, send=send)


__all__ = ["VideoUploadGuard"]
