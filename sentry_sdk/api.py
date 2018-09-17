import inspect
from contextlib import contextmanager

from sentry_sdk.hub import Hub
from sentry_sdk.scope import Scope
from sentry_sdk.transport import Transport, HttpTransport
from sentry_sdk.client import Client, get_options
from sentry_sdk.integrations import setup_integrations


__all__ = ["Hub", "Scope", "Client", "Transport", "HttpTransport"]


def public(f):
    __all__.append(f.__name__)
    return f


def hubmethod(f):
    f.__doc__ = "%s\n\n%s" % (
        "Alias for `Hub.%s`" % f.__name__,
        inspect.getdoc(getattr(Hub, f.__name__)),
    )
    return public(f)


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
    setup_integrations(
        options["integrations"] or [], with_defaults=options["default_integrations"]
    )
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


@hubmethod
def capture_event(event, hint=None):
    hub = Hub.current
    if hub is not None:
        return hub.capture_event(event, hint)


@hubmethod
def capture_message(message, level=None):
    hub = Hub.current
    if hub is not None:
        return hub.capture_message(message, level)


@hubmethod
def capture_exception(error=None):
    hub = Hub.current
    if hub is not None:
        return hub.capture_exception(error)


@hubmethod
def add_breadcrumb(*args, **kwargs):
    hub = Hub.current
    if hub is not None:
        return hub.add_breadcrumb(*args, **kwargs)


@hubmethod
def configure_scope(callback=None):
    hub = Hub.current
    if hub is not None:
        return hub.configure_scope(callback)
    elif callback is None:

        @contextmanager
        def inner():
            yield Scope()

        return inner()


@hubmethod
def push_scope(callback=None):
    hub = Hub.current
    if hub is not None:
        return hub.push_scope(callback)
    elif callback is None:

        @contextmanager
        def inner():
            yield Scope()

        return inner()


@hubmethod
def last_event_id():
    hub = Hub.current
    if hub is not None:
        return hub.last_event_id()
