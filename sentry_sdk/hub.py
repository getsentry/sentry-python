import sys
import copy
import linecache
from threading import local
from contextlib import contextmanager

from ._compat import with_metaclass
from .scope import Scope
from .utils import exceptions_from_error_tuple, create_event, \
    skip_internal_frames


_local = local()


class HubMeta(type):

    @property
    def current(self):
        try:
            rv = _local.hub
        except AttributeError:
            _local.hub = rv = Hub(GLOBAL_HUB)
        return rv

    @property
    def main(self):
        return GLOBAL_HUB


class _HubManager(object):

    def __init__(self, hub):
        self._old = Hub.current
        _local.hub = hub

    def __exit__(self, exc_type, exc_value, tb):
        _local.hub = self._old


class _ScopeManager(object):

    def __init__(self, hub):
        self._hub = hub

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self._hub._stack.pop()


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
            return event.get('event_id')

    def capture_message(self, message, level=None):
        """Captures a message."""
        if self.client is None:
            return
        if level is None:
            level = 'info'
        event = create_event()
        event['message'] = message
        if level is not None:
            event['level'] = level
        return self.capture_event(event)

    def capture_exception(self, error=None):
        """Captures an exception."""
        client = self.client
        if client is None:
            return
        if error is None:
            exc_type, exc_value, tb = sys.exc_info()
        else:
            tb = getattr(error, '__traceback__', None)
            if tb is not None:
                exc_type = type(error)
                exc_value = error
            else:
                exc_type, exc_value, tb = sys.exc_info()
                tb = skip_internal_frames(tb)
                if exc_value is not error:
                    tb = None
                    exc_value = error
                    exc_type = type(error)

        event = create_event()
        event['exception'] = {
            'values': exceptions_from_error_tuple(
                exc_type, exc_value, tb, client.options['with_locals'])
        }
        return self.capture_event(event)

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
            scope.breadcrumbs.append(crumb)
        while len(scope.breadcrumbs) >= client.options['max_breadcrumbs']:
            scope.breadcrumbs.popleft()

    def add_event_processor(self, factory):
        """Registers a new event processor with the top scope."""
        if self._stack[1][0] is not None:
            self._pending_processors.append(factory)

    def push_scope(self):
        """Pushes a new layer on the scope stack."""
        self._flush_event_processors()
        client, scope = self._stack[-1]
        self._stack.append((client, copy.copy(scope)))
        return _ScopeManager(self)

    @contextmanager
    def configure_scope(self):
        """Reconfigures the scope."""
        yield self._stack[-1][1]

    def _flush_event_processors(self):
        rv = self._pending_processors
        self._pending_processors = []
        top = self._stack[-1][1]
        for factory in rv:
            top._event_processors.append(factory())


GLOBAL_HUB = Hub()
