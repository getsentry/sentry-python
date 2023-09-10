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

            for key, value in hub.iter_trace_propagation_headers():
                # Currently broken
                # Waiting for response here
                # https://github.com/grpc/grpc/issues/34298
                client_call_details.metadata.add(key, value)

            response = await continuation(client_call_details, request)
            status_code = await response.code()
            span.set_data("code", status_code.name)

            return response
