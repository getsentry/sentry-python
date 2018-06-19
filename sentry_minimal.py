from contextlib import contextmanager


__all__ = []


def public(f):
    __all__.append(f.__name__)
    return f


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


@public
@contextmanager
def configure_scope():
    hub = Hub.current
    if hub is not None:
        with hub.configure_scope() as scope:
            yield scope
    else:
        yield Scope()


try:
    from sentry_sdk import Hub, Scope
except ImportError:
    class Hub(object):
        current = main = None
