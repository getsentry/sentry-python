import socket

import sentry_sdk
from sentry_sdk._types import MYPY
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration
from sentry_sdk.tracing_utils import has_span_streaming_enabled

if MYPY:
    from socket import AddressFamily, SocketKind
    from typing import List, Optional, Tuple, Union

__all__ = ["SocketIntegration"]


class SocketIntegration(Integration):
    identifier = "socket"
    origin = f"auto.socket.{identifier}"

    @staticmethod
    def setup_once() -> None:
        """
        patches two of the most used functions of socket: create_connection and getaddrinfo(dns resolver)
        """
        _patch_create_connection()
        _patch_getaddrinfo()


def _get_span_description(
    host: "Union[bytes, str, None]", port: "Union[bytes, str, int, None]"
) -> str:
    try:
        host = host.decode()  # type: ignore
    except (UnicodeDecodeError, AttributeError):
        pass

    try:
        port = port.decode()  # type: ignore
    except (UnicodeDecodeError, AttributeError):
        pass

    description = "%s:%s" % (host, port)  # type: ignore
    return description


def _patch_create_connection() -> None:
    real_create_connection = socket.create_connection

    def create_connection(
        address: "Tuple[Optional[str], int]",
        timeout: "Optional[float]" = socket._GLOBAL_DEFAULT_TIMEOUT,  # type: ignore
        source_address: "Optional[Tuple[Union[bytearray, bytes, str], int]]" = None,
    ) -> "socket.socket":
        client = sentry_sdk.get_client()
        integration = client.get_integration(SocketIntegration)
        if integration is None:
            return real_create_connection(address, timeout, source_address)

        if has_span_streaming_enabled(client.options):
            if sentry_sdk.traces.get_current_span() is None:
                return real_create_connection(address, timeout, source_address)

            with sentry_sdk.traces.start_span(
                name=_get_span_description(address[0], address[1]),
                attributes={
                    "sentry.op": OP.SOCKET_CONNECTION,
                    "sentry.origin": SocketIntegration.origin,
                },
            ) as span:
                if address[0] is not None:
                    span.set_attribute(SPANDATA.SERVER_ADDRESS, address[0])
                span.set_attribute(SPANDATA.SERVER_PORT, address[1])

                return real_create_connection(
                    address=address, timeout=timeout, source_address=source_address
                )
        else:
            with sentry_sdk.start_span(
                op=OP.SOCKET_CONNECTION,
                name=_get_span_description(address[0], address[1]),
                origin=SocketIntegration.origin,
            ) as span:
                span.set_data("address", address)
                span.set_data("timeout", timeout)
                span.set_data("source_address", source_address)

                return real_create_connection(
                    address=address, timeout=timeout, source_address=source_address
                )

    socket.create_connection = create_connection  # type: ignore


def _patch_getaddrinfo() -> None:
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(
        host: "Union[bytes, str, None]",
        port: "Union[bytes, str, int, None]",
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> "List[Tuple[AddressFamily, SocketKind, int, str, Union[Tuple[str, int], Tuple[str, int, int, int], Tuple[int, bytes]]]]":
        client = sentry_sdk.get_client()
        integration = client.get_integration(SocketIntegration)
        if integration is None:
            return real_getaddrinfo(host, port, family, type, proto, flags)

        if has_span_streaming_enabled(client.options):
            if sentry_sdk.traces.get_current_span() is None:
                return real_getaddrinfo(host, port, family, type, proto, flags)

            with sentry_sdk.traces.start_span(
                name=_get_span_description(host, port),
                attributes={
                    "sentry.op": OP.SOCKET_DNS,
                    "sentry.origin": SocketIntegration.origin,
                },
            ) as span:
                if isinstance(host, str):
                    span.set_attribute(SPANDATA.SERVER_ADDRESS, host)
                elif isinstance(host, bytes):
                    span.set_attribute(
                        SPANDATA.SERVER_ADDRESS, host.decode(errors="replace")
                    )

                if isinstance(port, int):
                    span.set_attribute(SPANDATA.SERVER_PORT, port)
                elif port is not None:
                    try:
                        span.set_attribute(SPANDATA.SERVER_PORT, int(port))
                    except (ValueError, TypeError):
                        pass

                return real_getaddrinfo(host, port, family, type, proto, flags)
        else:
            with sentry_sdk.start_span(
                op=OP.SOCKET_DNS,
                name=_get_span_description(host, port),
                origin=SocketIntegration.origin,
            ) as span:
                span.set_data("host", host)
                span.set_data("port", port)

                return real_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = getaddrinfo
