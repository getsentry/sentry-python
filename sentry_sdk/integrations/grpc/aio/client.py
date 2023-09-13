from typing import Callable, Union

from grpc.aio import UnaryUnaryClientInterceptor
from grpc.aio._call import UnaryUnaryCall
from grpc.aio._interceptor import ClientCallDetails
from google.protobuf.message import Message

from sentry_sdk import Hub
from sentry_sdk.consts import OP


class AsyncClientInterceptor(UnaryUnaryClientInterceptor):
    async def intercept_unary_unary(
        self,
        continuation: Callable[[ClientCallDetails, Message], UnaryUnaryCall],
        client_call_details: ClientCallDetails,
        request: Message,
    ) -> Union[UnaryUnaryCall, Message]:
        hub = Hub.current
        method = client_call_details.method

        with hub.start_span(
            op=OP.GRPC_CLIENT, description="unary unary call to %s" % method.decode()
        ) as span:
            span.set_data("type", "unary unary")
            span.set_data("method", method)

            client_call_details = self._update_client_call_details_metadata_from_hub(
                client_call_details, hub
            )

            response = await continuation(client_call_details, request)
            status_code = await response.code()
            span.set_data("code", status_code.name)

            return response

    @staticmethod
    def _update_client_call_details_metadata_from_hub(
        client_call_details: ClientCallDetails, hub: Hub
    ):
        metadata = (
            list(client_call_details.metadata) if client_call_details.metadata else []
        )
        for key, value in hub.iter_trace_propagation_headers():
            metadata.append((key, value))

        client_call_details = ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )

        return client_call_details
