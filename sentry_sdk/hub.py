import sys
import copy
from contextlib import contextmanager

from ._compat import with_metaclass
from .scope import Scope
from .utils import skip_internal_frames, ContextVar
from .event import Event


_local = ContextVar("sentry_current_hub")


@contextmanager
def _internal_exceptions():
    try:
        yield
    except Exception:
        Hub.current.capture_internal_exception()


def _should_send_default_pii():
    client = Hub.current.client
    if not client:
        return False
    return client.options["send_default_pii"]


class HubMeta(type):
    @property
    def current(self):
        rv = _local.get(None)
        if rv is None:
            rv = Hub(GLOBAL_HUB)
            _local.set(rv)
        return rv

    @property
    def main(self):
        return GLOBAL_HUB


class _HubManager(object):
    def __init__(self, hub):
        self._old = Hub.current
        _local.set(hub)

    def __exit__(self, exc_type, exc_value, tb):
        _local.set(self._old)


class _ScopeManager(object):
    def __init__(self, hub, layer):
        self._hub = hub
        self._layer = layer

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        assert self._hub.pop_scope_unsafe() == self._layer, "popped wrong scope"


class Hub(with_metaclass(HubMeta)):
    def __init__(self, client_or_hub=None, scope=None):
        if isinstance(client_or_hub, Hub):
            hub = client_or_hub
            client, other_scope = hub._stack[-1]
            if scope is None:
                hub._flush_event_processors()
                scope = copy.copy(other_scope)
        else:
            client = client_or_hub
        if scope is None:
            scope = Scope()
        self._stack = [(client, scope)]
        self._pending_processors = []

    def __enter__(self):
        return _HubManager(self)

    def run(self, callback):
        """Runs a callback in the context of the hub.  Alternatively the
        with statement can be used on the hub directly.
        """
        with self:
            callback()

    @property
    def client(self):
        """Returns the current client on the hub."""
        return self._stack[-1][0]

    def bind_client(self, new):
        """Binds a new client to the hub."""
        top = self._stack[-1]
        self._stack[-1] = (new, top[1])

    def capture_event(self, event):
        """Captures an event."""
        self._flush_event_processors()
        client, scope = self._stack[-1]
        if client is not None:
            client.capture_event(event, scope)
            return event.get("event_id")

    def capture_message(self, message, level=None):
        """Captures a message."""
        if self.client is None:
            return
        if level is None:
            level = "info"
        event = Event()
        event["message"] = message
        if level is not None:
            event["level"] = level
        return self.capture_event(event)

    def capture_exception(self, error=None):
        """Captures an exception."""
        client = self.client
        if client is None:
            return
        if error is None:
            exc_type, exc_value, tb = sys.exc_info()
        elif isinstance(error, tuple) and len(error) == 3:
            exc_type, exc_value, tb = error
        else:
            tb = getattr(error, "__traceback__", None)
            if tb is not None:
                exc_type = type(error)
                exc_value = error
            else:
                exc_type, exc_value, tb = sys.exc_info()
                if exc_value is not error:
                    tb = None
                    exc_value = error
                    exc_type = type(error)

        if tb is not None:
            tb = skip_internal_frames(tb)

        event = Event()
        try:
            event.set_exception(exc_type, exc_value, tb, client.options["with_locals"])
            return self.capture_event(event)
        except Exception:
            self.capture_internal_exception()

    def capture_internal_exception(self, error=None):
        """Capture an exception that is likely caused by a bug in the SDK
        itself."""
        pass

    def add_breadcrumb(self, crumb):
        """Adds a breadcrumb."""
        client, scope = self._stack[-1]
        if client is None:
            return
        if callable(crumb):
            crumb = crumb()
        if crumb is not None:
            scope._breadcrumbs.append(crumb)
        while len(scope._breadcrumbs) >= client.options["max_breadcrumbs"]:
            scope._breadcrumbs.popleft()

    def add_event_processor(self, factory):
        """Registers a new event processor with the top scope."""
        self._pending_processors.append(factory)

    def push_scope(self):
        """Pushes a new layer on the scope stack. Returns a context manager
        that should be used to pop the scope again."""
        self._flush_event_processors()
        client, scope = self._stack[-1]
        new_layer = (client, copy.copy(scope))
        self._stack.append(new_layer)
        return _ScopeManager(self, new_layer)

    def pop_scope_unsafe(self):
        """Pops a scope layer from the stack. Try to use the context manager
        `push_scope()` instead."""
        self._pending_processors = []
        rv = self._stack.pop()
        assert self._stack
        return rv

    def configure_scope(self, callback=None):
        """Reconfigures the scope."""
        client, scope = self._stack[-1]
        if callback is not None:
            if client is not None and scope is not None:
                callback(scope)
        else:

            @contextmanager
            def inner():
                if client is not None and scope is not None:
                    yield scope
                else:
                    yield Scope()

            return inner()

    def _flush_event_processors(self):
        rv = self._pending_processors
        self._pending_processors = []
        top = self._stack[-1][1]
        for factory in rv:
            top._event_processors.append(factory())


GLOBAL_HUB = Hub()
