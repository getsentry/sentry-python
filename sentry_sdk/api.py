import atexit

from .hub import Hub
from .client import Client


__all__ = ['Hub', 'Client']


def public(f):
    __all__.append(f.__name__)
    return f


class _InitGuard(object):

    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        c = self._client
        if c is not None:
            c.close()


@public
def init(*args, **kwargs):
    client = Client(*args, **kwargs)
    if client.dsn is not None:
        Hub.main.bind_client(client)
    return _InitGuard(client)


@public
def capture_event(event):
    hub = Hub.current
    if hub is not None:
        return hub.capture_event(event)


@public
def capture_message(message, level=None):
    hub = Hub.current
    if hub is not None:
        return hub.capture_message(message, level)


@public
def capture_exception(error=None):
    hub = Hub.current
    if hub is not None:
        return hub.capture_exception(error)


@public
def add_breadcrumb(crumb):
    hub = Hub.current
    if hub is not None:
        return hub.add_breadcrumb(crumb)
