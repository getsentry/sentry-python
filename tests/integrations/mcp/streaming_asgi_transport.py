import asyncio
from httpx import ASGITransport, Request, Response, AsyncByteStream
import anyio

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, MutableMapping


class StreamingASGITransport(ASGITransport):
    """
    Simple transport whose only purpose is to keep GET request alive in SSE connections, allowing
    tests involving SSE interactions to run in-process.
    """

    def __init__(
        self,
        app: "Callable",
        keep_sse_alive: "asyncio.Event",
    ) -> None:
        self.keep_sse_alive = keep_sse_alive
        super().__init__(app)

    async def handle_async_request(self, request: "Request") -> "Response":
        scope = {
            "type": "http",
            "method": request.method,
            "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
            "path": request.url.path,
            "query_string": request.url.query,
        }

        is_streaming_sse = scope["method"] == "GET" and scope["path"] == "/sse"
        if not is_streaming_sse:
            return await super().handle_async_request(request)

        request_body = b""
        if request.content:
            request_body = await request.aread()

        body_sender, body_receiver = anyio.create_memory_object_stream[bytes](0)

        async def receive() -> "dict[str, Any]":
            if self.keep_sse_alive.is_set():
                return {"type": "http.disconnect"}

            await self.keep_sse_alive.wait()  # Keep alive :)
            return {"type": "http.request", "body": request_body, "more_body": False}

        async def send(message: "MutableMapping[str, Any]") -> None:
            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = message.get("more_body", False)

                if body == b"" and not more_body:
                    return

                if body:
                    await body_sender.send(body)

                if not more_body:
                    await body_sender.aclose()

        async def run_app():
            await self.app(scope, receive, send)

        class StreamingBodyStream(AsyncByteStream):
            def __init__(self, receiver):
                self.receiver = receiver

            async def __aiter__(self):
                try:
                    async for chunk in self.receiver:
                        yield chunk
                except anyio.EndOfStream:
                    pass

        stream = StreamingBodyStream(body_receiver)
        response = Response(status_code=200, headers=[], stream=stream)

        asyncio.create_task(run_app())
        return response
