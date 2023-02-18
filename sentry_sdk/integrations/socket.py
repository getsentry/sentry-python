import socket

from sentry_sdk import Hub
from sentry_sdk.consts import OP
from sentry_sdk.integrations import Integration

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


def _patch_create_connection():
    # type: () -> None
    real_create_connection = socket.create_connection

    def create_connection(
        address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None
    ):
        # type: (tuple[str, None | int], float | None, tuple[bytearray | bytes | str, int] | None) -> socket
        hub = Hub.current
        if hub.get_integration(SocketIntegration) is None:
            return real_create_connection(
                address=address, timeout=timeout, source_address=source_address
            )

        with hub.start_span(
            op=OP.SOCKET_CONNECTION, description="%s:%s" % (address[0], address[1])
        ) as span:
            span.set_data("address", address)
            span.set_data("timeout", timeout)
            span.set_data("source_address", source_address)

            return real_create_connection(
                address=address, timeout=timeout, source_address=source_address
            )

    socket.create_connection = create_connection


def _patch_getaddrinfo():
    # type: () -> None
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        hub = Hub.current
        if hub.get_integration(SocketIntegration) is None:
            return real_getaddrinfo(host, port, *args, **kwargs)

        with hub.start_span(
            op=OP.SOCKET_DNS, description="%s:%s" % (host, port)
        ) as span:
            span.set_data("host", host)
            span.set_data("port", port)

            return real_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo
