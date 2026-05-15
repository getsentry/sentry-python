from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.grpc.consts import SPAN_ORIGIN
from sentry_sdk.tracing import TransactionSource
from sentry_sdk.tracing_utils import has_span_streaming_enabled

if TYPE_CHECKING:
    from typing import Callable, Optional

    from google.protobuf.message import Message

try:
    import grpc
    from grpc import HandlerCallDetails, RpcMethodHandler, ServicerContext
except ImportError:
    raise DidNotEnable("grpcio is not installed")


class ServerInterceptor(grpc.ServerInterceptor):  # type: ignore
    def __init__(
        self: "ServerInterceptor",
        find_name: "Optional[Callable[[ServicerContext], str]]" = None,
    ) -> None:
        self._custom_find_name = find_name

        super().__init__()

    def intercept_service(
        self: "ServerInterceptor",
        continuation: "Callable[[HandlerCallDetails], RpcMethodHandler]",
        handler_call_details: "HandlerCallDetails",
    ) -> "RpcMethodHandler":
        handler = continuation(handler_call_details)
        if not handler or not handler.unary_unary:
            return handler

        method_name = handler_call_details.method
        custom_find_name = self._custom_find_name

        def behavior(request: "Message", context: "ServicerContext") -> "Message":
            with sentry_sdk.isolation_scope():
                name = custom_find_name(context) if custom_find_name else method_name

                if name:
                    metadata = dict(context.invocation_metadata())

                    span_streaming = has_span_streaming_enabled(
                        sentry_sdk.get_client().options
                    )
                    if span_streaming:
                        sentry_sdk.traces.continue_trace(metadata)

                        with sentry_sdk.traces.start_span(
                            name=name,
                            attributes={
                                "sentry.op": OP.GRPC_SERVER,
                                "sentry.span.source": TransactionSource.CUSTOM.value,
                                "sentry.origin": SPAN_ORIGIN,
                            },
                            parent_span=None,
                        ):
                            try:
                                return handler.unary_unary(request, context)
                            except BaseException as e:
                                raise e
                    else:
                        transaction = sentry_sdk.continue_trace(
                            metadata,
                            op=OP.GRPC_SERVER,
                            name=name,
                            source=TransactionSource.CUSTOM,
                            origin=SPAN_ORIGIN,
                        )

                        with sentry_sdk.start_transaction(transaction=transaction):
                            try:
                                return handler.unary_unary(request, context)
                            except BaseException as e:
                                raise e
                else:
                    return handler.unary_unary(request, context)

        return grpc.unary_unary_rpc_method_handler(
            behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
