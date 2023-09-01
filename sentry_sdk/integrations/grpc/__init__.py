from typing import List, Optional, Sequence

from grpc import Channel, Server
from grpc.aio import Channel as AsyncChannel

from sentry_sdk.integrations import Integration
from .client import ClientInterceptor
from .server import ServerInterceptor
from .aio.server import ServerInterceptor as AsyncServerInterceptor
from .aio.client import AsyncClientInterceptor


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

    old_aio_insecure_channel = grpc.aio.insecure_channel

    def sentry_patched_insecure_aio_channel(
        *args,
        interceptors: Optional[Sequence[grpc.aio.ClientInterceptor]] = None,
        **kwargs
    ) -> AsyncChannel:
        interceptor = AsyncClientInterceptor()
        if interceptors is None:
            interceptors = [interceptor]
        else:
            interceptors.append(interceptor)
        return old_aio_insecure_channel(*args, interceptors=interceptors, **kwargs)

    grpc.aio.insecure_channel = sentry_patched_insecure_aio_channel

    old_aio_secure_channel = grpc.aio.secure_channel

    def sentry_patched_secure_channel(
        *args,
        interceptors: Optional[Sequence[grpc.aio.ClientInterceptor]] = None,
        **kwargs
    ) -> AsyncChannel:
        interceptor = AsyncClientInterceptor()
        if interceptors is None:
            interceptors = [interceptor]
        else:
            interceptors.append(interceptor)
        return old_aio_secure_channel(*args, interceptors=interceptors, **kwargs)

    grpc.aio.secure_channel = sentry_patched_secure_channel


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

    old_aio_server = grpc.aio.server

    def sentry_patched_aio_server(
        *args, interceptors: Optional[List[grpc.ServerInterceptor]] = None, **kwargs
    ) -> Server:
        server_interceptor = AsyncServerInterceptor(
            find_name=lambda request: request.__class__
        )
        if interceptors is None:
            interceptors = [server_interceptor]
        else:
            interceptors.append(server_interceptor)

        return old_aio_server(*args, interceptors=interceptors, **kwargs)

    grpc.server = sentry_patched_server
    grpc.aio.server = sentry_patched_aio_server


class GRPCIntegration(Integration):
    identifier = "grpc"

    @staticmethod
    def setup_once() -> None:
        patch_grpc_channels()
        patch_grpc_server()
