"""Bound the transport body before multipart parsing can spool unlimited data."""

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import get_settings


class RequestBodyLimit:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        maximum = get_settings().max_upload_bytes + 1024 * 1024
        headers = Headers(scope=scope)
        length = headers.get("content-length")
        if length is not None:
            try:
                declared = int(length)
                if declared < 0:
                    raise ValueError("negative length")
            except ValueError:
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(
                    scope, receive, send
                )
                return
            if declared > maximum:
                await JSONResponse({"detail": "Request exceeds upload limit"}, status_code=413)(
                    scope, receive, send
                )
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > maximum:
                raise HTTPException(413, "Request exceeds upload limit")
            return message

        await self.app(scope, limited_receive, send)
