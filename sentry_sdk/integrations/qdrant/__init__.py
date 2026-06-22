from sentry_sdk.integrations import DidNotEnable

try:
    from qdrant_client.http import ApiClient, AsyncApiClient
    import grpc
except ImportError:
    raise DidNotEnable("Qdrant client not installed")

from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.qdrant.consts import _IDENTIFIER
from sentry_sdk.integrations.qdrant.qdrant import (
    _sync_api_client_send_inner,
    _async_api_client_send_inner,
    _wrap_channel_sync,
    _wrap_channel_async,
)


class QdrantIntegration(Integration):
    identifier = _IDENTIFIER

    def __init__(self, mute_children_http_spans=True):
        # type: (bool) -> None
        self.mute_children_http_spans = mute_children_http_spans

    @staticmethod
    def setup_once():
        # type: () -> None

        # hooks for the REST client
        ApiClient.send_inner = _sync_api_client_send_inner(ApiClient.send_inner)
        AsyncApiClient.send_inner = _async_api_client_send_inner(
            AsyncApiClient.send_inner
        )

        # hooks for the gRPC client
        grpc.secure_channel = _wrap_channel_sync(grpc.secure_channel)
        grpc.insecure_channel = _wrap_channel_sync(grpc.insecure_channel)
        grpc.aio.secure_channel = _wrap_channel_async(grpc.aio.secure_channel)
        grpc.aio.insecure_channel = _wrap_channel_async(grpc.aio.insecure_channel)
