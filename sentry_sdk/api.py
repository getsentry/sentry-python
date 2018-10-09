import inspect
from contextlib import contextmanager

from sentry_sdk.hub import Hub, init
from sentry_sdk.scope import Scope
from sentry_sdk.transport import Transport, HttpTransport
from sentry_sdk.client import Client


__all__ = ["Hub", "Scope", "Client", "Transport", "HttpTransport", "init"]


_initial_client = None


def public(f):
    __all__.append(f.__name__)
    return f


def hubmethod(f):
    f.__doc__ = "%s\n\n%s" % (
        "Alias for `Hub.%s`" % f.__name__,
        inspect.getdoc(getattr(Hub, f.__name__)),
    )
    return public(f)


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
