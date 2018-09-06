from contextlib import contextmanager

from .utils import EventHint
from .hub import Hub
from .scope import Scope
from .client import Client, get_options
from .integrations import setup_integrations


__all__ = ["Hub", "Scope", "Client", "EventHint"]


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


def _init_on_hub(hub, args, kwargs):
    options = get_options(*args, **kwargs)
    client = Client(options)
    hub.bind_client(client)
    setup_integrations(options)
    return _InitGuard(client)


@public
def init(*args, **kwargs):
    """Initializes the SDK and optionally integrations.

    This takes the same arguments as the client constructor.
    """
    return _init_on_hub(Hub.main, args, kwargs)


def _init_on_current(*args, **kwargs):
    # This function only exists to support unittests.  Do not call it as
    # initializing integrations on anything but the main hub is not going
    # to yield the results you expect.
    return _init_on_hub(Hub.current, args, kwargs)


@public
def capture_event(event, hint=None):
    """Alias for `Hub.current.capture_event`"""
    hub = Hub.current
    if hub is not None:
        return hub.capture_event(event, hint)


@public
def capture_message(message, level=None):
    """Alias for `Hub.current.capture_message`"""
    hub = Hub.current
    if hub is not None:
        return hub.capture_message(message, level)


@public
def capture_exception(error=None):
    """Alias for `Hub.current.capture_exception`"""
    hub = Hub.current
    if hub is not None:
        return hub.capture_exception(error)


@public
def add_breadcrumb(*args, **kwargs):
    """Alias for `Hub.current.add_breadcrumb`"""
    hub = Hub.current
    if hub is not None:
        return hub.add_breadcrumb(*args, **kwargs)


@public
def configure_scope(callback=None):
    """Alias for `Hub.current.configure_scope`"""
    hub = Hub.current
    if hub is not None:
        return hub.configure_scope(callback)
    elif callback is None:

        @contextmanager
        def inner():
            yield Scope()

        return inner()


@public
def get_current_hub():
    """Alias for `Hub.current`"""
    return Hub.current


@public
def last_event_id():
    """Alias for `Hub.last_event_id`"""
    hub = Hub.current
    if hub is not None:
        return hub.last_event_id()
