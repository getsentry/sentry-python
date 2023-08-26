from typing import List, Optional

from grpc import Channel, Server

from sentry_sdk.integrations import Integration
from .client import ClientInterceptor
from .server import ServerInterceptor


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


def patch_grpc_server() -> None:
    """Monkeypatch grpc.server to add server-side interceptor."""
    import grpc

    old_server = grpc.server

    def sentry_patched_server(
        *args, interceptors: Optional[List[grpc.ServerInterceptor]] = None, **kwargs
    ) -> Server:
        server_interceptor = ServerInterceptor()
        if interceptors is None:
            interceptors = [server_interceptor]
        else:
            interceptors.append(server_interceptor)

        return old_server(*args, interceptors=interceptors, **kwargs)

    grpc.server = sentry_patched_server


class GRPCIntegration(Integration):
    identifier = "grpc"

    @staticmethod
    def setup_once() -> None:
        patch_grpc_channels()
        patch_grpc_server()
