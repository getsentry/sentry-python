from grpc import Channel

from sentry_sdk.integrations import Integration
from .client import ClientInterceptor


def patch_grpc_channels() -> None:
    """Monkeypatch grpc.secure_channel and grpc.insecure_channel
    with functions intercepting the returned channels.
    """
    import grpc

    old_insecure_channel = grpc.insecure_channel

    def sentry_patched_insecure_channel(*args, **kwargs) -> Channel:
        channel = old_insecure_channel(*args, **kwargs)
        return grpc.intercept_channel(channel, ClientInterceptor())

    grpc.insecure_channel = sentry_patched_insecure_channel

    old_secure_channel = grpc.secure_channel

    def sentry_patched_secure_channel(*args, **kwargs) -> Channel:
        channel = old_secure_channel(*args, **kwargs)
        return grpc.intercept_channel(channel, ClientInterceptor())

    grpc.secure_channel = sentry_patched_secure_channel


class GRPCIntegration(Integration):
    identifier = "grpc"

    @staticmethod
    def setup_once() -> None:
        patch_grpc_channels()
