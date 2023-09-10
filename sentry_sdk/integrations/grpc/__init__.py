from typing import List, Optional, Sequence, Callable, ParamSpec
from functools import wraps

import grpc
from grpc import Channel, Server, intercept_channel
from grpc.aio import Channel as AsyncChannel
from grpc.aio import Server as AsyncServer

from sentry_sdk.integrations import Integration
from .client import ClientInterceptor
from .server import ServerInterceptor
from .aio.server import ServerInterceptor as AsyncServerInterceptor
from .aio.client import AsyncClientInterceptor

P = ParamSpec("P")


def _wrap_channel_sync(func: Callable[P, Channel]) -> Callable[P, Channel]:
    "Wrapper for synchronous secure and insecure channel."

    @wraps(func)
    def patched_channel(*args, **kwargs) -> Channel:
        channel = func(*args, **kwargs)
        return intercept_channel(channel, ClientInterceptor())

    return patched_channel


def _wrap_channel_async(func: Callable[P, AsyncChannel]) -> Callable[P, AsyncChannel]:
    "Wrapper for asynchronous secure and insecure channel."

    @wraps(func)
    def patched_channel(
        *args,
        interceptors: Optional[Sequence[grpc.aio.ClientInterceptor]] = None,
        **kwargs
    ) -> Channel:
        interceptor = AsyncClientInterceptor()
        interceptors = [interceptor, *(interceptors or [])]
        return func(*args, interceptors=interceptors, **kwargs)

    return patched_channel


def _wrap_sync_server(func: Callable[P, Server]) -> Callable[P, Server]:
    """Wrapper for synchronous server."""

    @wraps(func)
    def patched_server(
        *args, interceptors: Optional[List[grpc.ServerInterceptor]] = None, **kwargs
    ) -> Server:
        server_interceptor = ServerInterceptor()
        interceptors = [server_interceptor, *(interceptors or [])]
        return func(*args, interceptors=interceptors, **kwargs)

    return patched_server


def _wrap_async_server(func: Callable[P, AsyncServer]) -> Callable[P, AsyncServer]:
    """Wrapper for asynchronous server."""

    @wraps(func)
    def patched_aio_server(
        *args, interceptors: Optional[List[grpc.ServerInterceptor]] = None, **kwargs
    ) -> Server:
        server_interceptor = AsyncServerInterceptor(
            find_name=lambda request: request.__class__
        )
        interceptors = [server_interceptor, *(interceptors or [])]
        return func(*args, interceptors=interceptors, **kwargs)

    return patched_aio_server


class GRPCIntegration(Integration):
    identifier = "grpc"

    @staticmethod
    def setup_once() -> None:
        import grpc

        grpc.insecure_channel = _wrap_channel_sync(grpc.insecure_channel)
        grpc.secure_channel = _wrap_channel_sync(grpc.secure_channel)

        grpc.aio.insecure_channel = _wrap_channel_async(grpc.aio.insecure_channel)
        grpc.aio.secure_channel = _wrap_channel_async(grpc.aio.secure_channel)

        grpc.server = _wrap_sync_server(grpc.server)
        grpc.aio.server = _wrap_async_server(grpc.aio.server)
