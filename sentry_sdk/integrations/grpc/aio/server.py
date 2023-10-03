from functools import wraps

from sentry_sdk import Hub
from sentry_sdk._types import MYPY
from sentry_sdk.consts import OP
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.tracing import Transaction, TRANSACTION_SOURCE_CUSTOM
from sentry_sdk.utils import event_from_exception

if MYPY:
    from collections.abc import Awaitable, Callable
    from typing import Any


try:
    import grpc
    from grpc import HandlerCallDetails, RpcMethodHandler
    from grpc.aio import ServicerContext
    from grpc.experimental import wrap_server_method_handler
except ImportError:
    raise DidNotEnable("grpcio is not installed")


class ServerInterceptor(grpc.aio.ServerInterceptor):  # type: ignore
    def __init__(self, find_name=None):
        # type: (ServerInterceptor, Callable[[ServicerContext], str] | None) -> None
        self._find_method_name = find_name or ServerInterceptor._find_name

        super(ServerInterceptor, self).__init__()

    async def intercept_service(self, continuation, handler_call_details):
        # type: (ServerInterceptor, Callable[[HandlerCallDetails], Awaitable[RpcMethodHandler]], HandlerCallDetails) -> Awaitable[RpcMethodHandler]
        handler = await continuation(handler_call_details)
        return wrap_server_method_handler(self.wrapper, handler)

    def wrapper(self, handler):
        # type: (ServerInterceptor, Callable[[Any, ServicerContext], Awaitable[Any]]) -> Callable[[Any, ServicerContext], Awaitable[Any]]
        @wraps(handler)
        async def wrapped(request, context):
            # type: (Any, ServicerContext) -> Any
            name = self._find_method_name(context)
            if not name:
                return await handler(request, context)

            hub = Hub(Hub.current)

            transaction = Transaction.continue_from_headers(
                dict(context.invocation_metadata()),
                op=OP.GRPC_SERVER,
                name=name,
                source=TRANSACTION_SOURCE_CUSTOM,
            )

            with hub.start_transaction(transaction=transaction):
                try:
                    return await handler(request, context)
                except Exception as exc:
                    event, hint = event_from_exception(
                        exc,
                        mechanism={"type": "grpc", "handled": False},
                    )
                    hub.capture_event(event, hint=hint)
                    raise

        return wrapped

    @staticmethod
    def _find_name(context):
        # type: (ServicerContext) -> str
        return context._rpc_event.call_details.method.decode()
