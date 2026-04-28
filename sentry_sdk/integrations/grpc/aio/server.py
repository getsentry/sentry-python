import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.grpc.consts import SPAN_ORIGIN
from sentry_sdk.tracing import TransactionSource
from sentry_sdk.utils import event_from_exception

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any, Optional


try:
    import grpc
    from grpc import HandlerCallDetails, RpcMethodHandler
    from grpc.aio import AbortError, ServicerContext
except ImportError:
    raise DidNotEnable("grpcio is not installed")


class ServerInterceptor(grpc.aio.ServerInterceptor):  # type: ignore
    def __init__(
        self: "ServerInterceptor",
        find_name: "Callable[[ServicerContext], str] | None" = None,
    ) -> None:
        self._custom_find_name = find_name

        super().__init__()

    async def intercept_service(
        self: "ServerInterceptor",
        continuation: "Callable[[HandlerCallDetails], Awaitable[RpcMethodHandler]]",
        handler_call_details: "HandlerCallDetails",
    ) -> "Optional[Awaitable[RpcMethodHandler]]":
        handler = await continuation(handler_call_details)
        if handler is None:
            return None

        method_name = handler_call_details.method
        custom_find_name = self._custom_find_name

        if not handler.request_streaming and not handler.response_streaming:
            handler_factory = grpc.unary_unary_rpc_method_handler

            async def wrapped(request: "Any", context: "ServicerContext") -> "Any":
                with sentry_sdk.isolation_scope():
                    name = (
                        custom_find_name(context) if custom_find_name else method_name
                    )
                    if not name:
                        return await handler(request, context)

                    # What if the headers are empty?
                    transaction = sentry_sdk.continue_trace(
                        dict(context.invocation_metadata()),
                        op=OP.GRPC_SERVER,
                        name=name,
                        source=TransactionSource.CUSTOM,
                        origin=SPAN_ORIGIN,
                    )

                    with sentry_sdk.start_transaction(transaction=transaction):
                        try:
                            return await handler.unary_unary(request, context)
                        except AbortError:
                            raise
                        except Exception as exc:
                            event, hint = event_from_exception(
                                exc,
                                mechanism={"type": "grpc", "handled": False},
                            )
                            sentry_sdk.capture_event(event, hint=hint)
                            raise

        elif not handler.request_streaming and handler.response_streaming:
            handler_factory = grpc.unary_stream_rpc_method_handler

            async def wrapped(request: "Any", context: "ServicerContext") -> "Any":  # type: ignore
                async for r in handler.unary_stream(request, context):
                    yield r

        elif handler.request_streaming and not handler.response_streaming:
            handler_factory = grpc.stream_unary_rpc_method_handler

            async def wrapped(request: "Any", context: "ServicerContext") -> "Any":
                response = handler.stream_unary(request, context)
                return await response

        elif handler.request_streaming and handler.response_streaming:
            handler_factory = grpc.stream_stream_rpc_method_handler

            async def wrapped(request: "Any", context: "ServicerContext") -> "Any":  # type: ignore
                async for r in handler.stream_stream(request, context):
                    yield r

        return handler_factory(
            wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
