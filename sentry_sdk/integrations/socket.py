import socket

import sentry_sdk
from sentry_sdk._types import MYPY
from sentry_sdk.consts import OP
from sentry_sdk.integrations import Integration
from sentry_sdk.utils import ensure_integration_enabled

if MYPY:
    from socket import AddressFamily, SocketKind
    from typing import Tuple, Optional, Union, List

__all__ = ["SocketIntegration"]


class SocketIntegration(Integration):
    identifier = "socket"

    @staticmethod
    def setup_once():
        # type: () -> None
        """
        patches two of the most used functions of socket: create_connection and getaddrinfo(dns resolver)
        """
        _patch_create_connection()
        _patch_getaddrinfo()


def _get_span_description(host, port):
    # type: (Union[bytes, str, None], Union[str, int, None]) -> str

    try:
        host = host.decode()  # type: ignore
    except (UnicodeDecodeError, AttributeError):
        pass

    description = "%s:%s" % (host, port)  # type: ignore

    return description


def _patch_create_connection():
    # type: () -> None
    real_create_connection = socket.create_connection

    @ensure_integration_enabled(SocketIntegration, real_create_connection)
    def create_connection(
        address,
        timeout=socket._GLOBAL_DEFAULT_TIMEOUT,  # type: ignore
        source_address=None,
    ):
        # type: (Tuple[Optional[str], int], Optional[float], Optional[Tuple[Union[bytearray, bytes, str], int]])-> socket.socket

        with sentry_sdk.start_span(
            op=OP.SOCKET_CONNECTION,
            description=_get_span_description(address[0], address[1]),
        ) as span:
            span.set_data("address", address)
            span.set_data("timeout", timeout)
            span.set_data("source_address", source_address)

            return real_create_connection(
                address=address, timeout=timeout, source_address=source_address
            )

    socket.create_connection = create_connection  # type: ignore


def _patch_getaddrinfo():
    # type: () -> None
    real_getaddrinfo = socket.getaddrinfo

    @ensure_integration_enabled(real_getaddrinfo)
    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        # type: (Union[bytes, str, None], Union[str, int, None], int, int, int, int) -> List[Tuple[AddressFamily, SocketKind, int, str, Union[Tuple[str, int], Tuple[str, int, int, int]]]]
        with sentry_sdk.start_span(
            op=OP.SOCKET_DNS, description=_get_span_description(host, port)
        ) as span:
            span.set_data("host", host)
            span.set_data("port", port)

            return real_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = getaddrinfo  # type: ignore
