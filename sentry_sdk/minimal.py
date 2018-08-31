from contextlib import contextmanager


__all__ = ["Scope", "Hub"]


def public(f):
    __all__.append(f.__name__)
    return f


@public
def capture_event(event, hint=None):
    hub = Hub.current
    if hub is not None:
        return hub.capture_event(event, hint)


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
def add_breadcrumb(*args, **kwargs):
    hub = Hub.current
    if hub is not None:
        return hub.add_breadcrumb(*args, **kwargs)


@public
def configure_scope(callback=None):
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
    return Hub.current


@public
def last_event_id():
    hub = Hub.current
    if hub is not None:
        return hub.last_event_id()


try:
    from sentry_sdk.hub import Hub
    from sentry_sdk.scope import Scope
except ImportError:

    class Hub(object):
        current = main = None

    class Scope(object):
        fingerprint = transaction = user = request = None

        def set_tag(self, key, value):
            pass

        def remove_tag(self, key):
            pass

        def set_context(self, key, value):
            pass

        def remove_context(self, key):
            pass

        def set_extra(self, key, value):
            pass

        def remove_extra(self, key):
            pass

        def clear(self):
            pass
