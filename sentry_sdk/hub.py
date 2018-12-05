import sys
import copy
import weakref
from datetime import datetime
from contextlib import contextmanager
from warnings import warn

from sentry_sdk._compat import with_metaclass, string_types
from sentry_sdk.scope import Scope
from sentry_sdk.client import Client
from sentry_sdk.utils import (
    exc_info_from_error,
    event_from_exception,
    logger,
    ContextVar,
)


_local = ContextVar("sentry_current_hub")
_initial_client = None


def _should_send_default_pii():
    client = Hub.current.client
    if not client:
        return False
    return client.options["send_default_pii"]


class _InitGuard(object):
    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        c = self._client
        if c is not None:
            c.close()


def init(*args, **kwargs):
    """Initializes the SDK and optionally integrations.

    This takes the same arguments as the client constructor.
    """
    global _initial_client
    client = Client(*args, **kwargs)
    Hub.current.bind_client(client)
    rv = _InitGuard(client)
    if client is not None:
        _initial_client = weakref.ref(client)
    return rv


class HubMeta(type):
    @property
    def current(self):
        """Returns the current instance of the hub."""
        rv = _local.get(None)
        if rv is None:
            rv = Hub(GLOBAL_HUB)
            _local.set(rv)
        return rv

    @property
    def main(self):
        """Returns the main instance of the hub."""
        return GLOBAL_HUB


class _HubManager(object):
    def __init__(self, hub):
        self._old = Hub.current
        _local.set(hub)

    def __exit__(self, exc_type, exc_value, tb):
        _local.set(self._old)


class _ScopeManager(object):
    def __init__(self, hub):
        self._hub = hub
        self._original_len = len(hub._stack)
        self._layer = hub._stack[-1]

    def __enter__(self):
        scope = self._layer[1]
        assert scope is not None
        return scope

    def __exit__(self, exc_type, exc_value, tb):
        current_len = len(self._hub._stack)
        if current_len < self._original_len:
            logger.error(
                "Scope popped too soon. Popped %s scopes too many.",
                self._original_len - current_len,
            )
            return
        elif current_len > self._original_len:
            logger.warning(
                "Leaked %s scopes: %s",
                current_len - self._original_len,
                self._hub._stack[self._original_len :],
            )

        layer = self._hub._stack[self._original_len - 1]
        del self._hub._stack[self._original_len - 1 :]

        if layer[1] != self._layer[1]:
            logger.error(
                "Wrong scope found. Meant to pop %s, but popped %s.",
                layer[1],
                self._layer[1],
            )
        elif layer[0] != self._layer[0]:
            warning = (
                "init() called inside of pushed scope. This might be entirely "
                "legitimate but usually occurs when initializing the SDK inside "
                "a request handler or task/job function. Try to initialize the "
                "SDK as early as possible instead."
            )
            logger.warning(warning)


class Hub(with_metaclass(HubMeta)):
    """The hub wraps the concurrency management of the SDK.  Each thread has
    its own hub but the hub might transfer with the flow of execution if
    context vars are available.

    If the hub is used with a with statement it's temporarily activated.
    """

    def __init__(self, client_or_hub=None, scope=None):
        if isinstance(client_or_hub, Hub):
            hub = client_or_hub
            client, other_scope = hub._stack[-1]
            if scope is None:
                scope = copy.copy(other_scope)
        else:
            client = client_or_hub
        if scope is None:
            scope = Scope()
        self._stack = [(client, scope)]
        self._last_event_id = None
        self._old_hubs = []

    def __enter__(self):
        self._old_hubs.append(Hub.current)
        _local.set(self)
        return self

    def __exit__(self, exc_type, exc_value, tb):
        old = self._old_hubs.pop()
        _local.set(old)

    def run(self, callback):
        """Runs a callback in the context of the hub.  Alternatively the
        with statement can be used on the hub directly.
        """
        with self:
            return callback()

    def get_integration(self, name_or_class):
        """Returns the integration for this hub by name or class.  If there
        is no client bound or the client does not have that integration
        then `None` is returned.

        If the return value is not `None` the hub is guaranteed to have a
        client attached.
        """
        if not isinstance(name_or_class, string_types):
            name_or_class = name_or_class.identifier
        client = self._stack[-1][0]
        if client is not None:
            rv = client.integrations.get(name_or_class)
            if rv is not None:
                return rv

        initial_client = _initial_client
        if initial_client is not None:
            initial_client = initial_client()

        if (
            initial_client is not None
            and initial_client is not client
            and initial_client.integrations.get(name_or_class) is not None
        ):
            warning = (
                "Integration %r attempted to run but it was only "
                "enabled on init() but not the client that "
                "was bound to the current flow.  Earlier versions of "
                "the SDK would consider these integrations enabled but "
                "this is no longer the case." % (name_or_class,)
            )
            warn(Warning(warning), stacklevel=3)
            logger.warning(warning)

    @property
    def client(self):
        """Returns the current client on the hub."""
        return self._stack[-1][0]

    def last_event_id(self):
        """Returns the last event ID."""
        return self._last_event_id

    def bind_client(self, new):
        """Binds a new client to the hub."""
        top = self._stack[-1]
        self._stack[-1] = (new, top[1])

    def capture_event(self, event, hint=None):
        """Captures an event.  The return value is the ID of the event.

        The event is a dictionary following the Sentry v7/v8 protocol
        specification.  Optionally an event hint dict can be passed that
        is used by processors to extract additional information from it.
        Typically the event hint object would contain exception information.
        """
        client, scope = self._stack[-1]
        if client is not None:
            rv = client.capture_event(event, hint, scope)
            if rv is not None:
                self._last_event_id = rv
            return rv

    def capture_message(self, message, level=None):
        """Captures a message.  The message is just a string.  If no level
        is provided the default level is `info`.
        """
        if self.client is None:
            return
        if level is None:
            level = "info"
        return self.capture_event({"message": message, "level": level})

    def capture_exception(self, error=None):
        """Captures an exception.

        The argument passed can be `None` in which case the last exception
        will be reported, otherwise an exception object or an `exc_info`
        tuple.
        """
        client = self.client
        if client is None:
            return
        if error is None:
            exc_info = sys.exc_info()
        else:
            exc_info = exc_info_from_error(error)

        event, hint = event_from_exception(exc_info, client_options=client.options)
        try:
            return self.capture_event(event, hint=hint)
        except Exception:
            self._capture_internal_exception(sys.exc_info())

    def _capture_internal_exception(self, exc_info):
        """Capture an exception that is likely caused by a bug in the SDK
        itself."""
        logger.error("Internal error in sentry_sdk", exc_info=exc_info)

    def add_breadcrumb(self, crumb=None, hint=None, **kwargs):
        """Adds a breadcrumb.  The breadcrumbs are a dictionary with the
        data as the sentry v7/v8 protocol expects.  `hint` is an optional
        value that can be used by `before_breadcrumb` to customize the
        breadcrumbs that are emitted.
        """
        client, scope = self._stack[-1]
        if client is None:
            logger.info("Dropped breadcrumb because no client bound")
            return

        crumb = dict(crumb or ())
        crumb.update(kwargs)
        if not crumb:
            return

        hint = dict(hint or ())

        if crumb.get("timestamp") is None:
            crumb["timestamp"] = datetime.utcnow()
        if crumb.get("type") is None:
            crumb["type"] = "default"

        original_crumb = crumb
        if client.options["before_breadcrumb"] is not None:
            crumb = client.options["before_breadcrumb"](crumb, hint)

        if crumb is not None:
            scope._breadcrumbs.append(crumb)
        else:
            logger.info("before breadcrumb dropped breadcrumb (%s)", original_crumb)
        while len(scope._breadcrumbs) > client.options["max_breadcrumbs"]:
            scope._breadcrumbs.popleft()

    def push_scope(self, callback=None):
        """Pushes a new layer on the scope stack. Returns a context manager
        that should be used to pop the scope again.  Alternatively a callback
        can be provided that is executed in the context of the scope.
        """
        if callback is not None:
            with self.push_scope() as scope:
                callback(scope)
            return

        client, scope = self._stack[-1]
        new_layer = (client, copy.copy(scope))
        self._stack.append(new_layer)

        return _ScopeManager(self)

    scope = push_scope

    def pop_scope_unsafe(self):
        """Pops a scope layer from the stack. Try to use the context manager
        `push_scope()` instead."""
        rv = self._stack.pop()
        assert self._stack, "stack must have at least one layer"
        return rv

    def configure_scope(self, callback=None):
        """Reconfigures the scope."""
        client, scope = self._stack[-1]
        if callback is not None:
            if client is not None:
                callback(scope)
            return

        @contextmanager
        def inner():
            if client is not None:
                yield scope
            else:
                yield Scope()

        return inner()


GLOBAL_HUB = Hub()
_local.set(GLOBAL_HUB)
