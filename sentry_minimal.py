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
def add_breadcrumb(*args, **kwargs):
    hub = Hub.current
    if hub is not None:
        return hub.add_breadcrumb(*args, **kwargs)


@public
@contextmanager
def configure_scope():
    hub = Hub.current
    if hub is not None:
        with hub.configure_scope() as scope:
            yield scope
    else:
        yield Scope()


@public
def get_current_hub():
    return Hub.current


try:
    from sentry_sdk.hub import Hub
    from sentry_sdk.scope import Scope
except ImportError:
    class Hub(object):
        current = main = None

    class Scope(object):
        fingerprint = transaction = user = None

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
