from __future__ import annotations

from sentry_sdk import Hub
from sentry_sdk._types import MYPY
from sentry_sdk.consts import OP
from sentry_sdk.integrations import DidNotEnable

if MYPY:
    from typing import Any, Callable, Iterator, Iterable, Union

try:
    import grpc
    from grpc import ClientCallDetails, Call
    from grpc._interceptor import _UnaryOutcome
    from grpc.aio._interceptor import UnaryStreamCall
    from google.protobuf.message import Message
except ImportError:
    raise DidNotEnable("grpcio is not installed")


class ClientInterceptor(
    grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor  # type: ignore
):
    _is_intercepted = False

    def intercept_unary_unary(
        self: ClientInterceptor,
        continuation: Callable[[ClientCallDetails, Message], _UnaryOutcome],
        client_call_details: ClientCallDetails,
        request: Message,
    ) -> _UnaryOutcome:
        hub = Hub.current
        method = client_call_details.method

        with hub.start_span(
            op=OP.GRPC_CLIENT, description="unary unary call to %s" % method
        ) as span:
            span.set_data("type", "unary unary")
            span.set_data("method", method)

            client_call_details = self._update_client_call_details_metadata_from_hub(
                client_call_details, hub
            )

            response = continuation(client_call_details, request)
            span.set_data("code", response.code().name)

            return response

    def intercept_unary_stream(
        self: ClientInterceptor,
        continuation: Callable[
            [ClientCallDetails, Message], Union[Iterable[Any], UnaryStreamCall]
        ],
        client_call_details: ClientCallDetails,
        request: Message,
    ) -> Union[Iterator[Message], Call]:
        hub = Hub.current
        method = client_call_details.method

        with hub.start_span(
            op=OP.GRPC_CLIENT, description="unary stream call to %s" % method
        ) as span:
            span.set_data("type", "unary stream")
            span.set_data("method", method)

            client_call_details = self._update_client_call_details_metadata_from_hub(
                client_call_details, hub
            )

            response: UnaryStreamCall = continuation(client_call_details, request)
            # Setting code on unary-stream leads to execution getting stuck
            # span.set_data("code", response.code().name)

            return response

    @staticmethod
    def _update_client_call_details_metadata_from_hub(
        client_call_details: ClientCallDetails, hub: Hub
    ) -> ClientCallDetails:
        metadata = (
            list(client_call_details.metadata) if client_call_details.metadata else []
        )
        for key, value in hub.iter_trace_propagation_headers():
            metadata.append((key, value))

        client_call_details = grpc._interceptor._ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
            compression=client_call_details.compression,
        )

        return client_call_details
