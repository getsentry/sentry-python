import copy
import random
import sys
import weakref

from datetime import datetime
from contextlib import contextmanager
from warnings import warn

from sentry_sdk._compat import with_metaclass
from sentry_sdk.scope import Scope
from sentry_sdk.client import Client
from sentry_sdk.tracing import Span, maybe_create_breadcrumbs_from_span
from sentry_sdk.utils import (
    exc_info_from_error,
    event_from_exception,
    logger,
    ContextVar,
)

MYPY = False
if MYPY:
    from contextlib import ContextManager
    from sys import _OptExcInfo

    from typing import Union
    from typing import Any
    from typing import Optional
    from typing import Tuple
    from typing import List
    from typing import Callable
    from typing import Generator
    from typing import Type
    from typing import TypeVar
    from typing import overload

    from sentry_sdk.integrations import Integration
    from sentry_sdk.utils import Event, Hint, Breadcrumb, BreadcrumbHint
    from sentry_sdk.consts import ClientConstructor

    T = TypeVar("T")

else:

    def overload(x):
        # type: (T) -> T
        return x


_local = ContextVar("sentry_current_hub")  # type: ignore
_initial_client = None  # type: Optional[weakref.ReferenceType[Client]]


def _should_send_default_pii():
    # type: () -> bool
    client = Hub.current.client
    if not client:
        return False
    return client.options["send_default_pii"]


class _InitGuard(object):
    def __init__(self, client):
        # type: (Client) -> None
        self._client = client

    def __enter__(self):
        # type: () -> _InitGuard
        return self

    def __exit__(self, exc_type, exc_value, tb):
        # type: (Any, Any, Any) -> None
        c = self._client
        if c is not None:
            c.close()


def _init(*args, **kwargs):
    # type: (*Optional[str], **Any) -> ContextManager[Any]
    """Initializes the SDK and optionally integrations.

    This takes the same arguments as the client constructor.
    """
    global _initial_client
    client = Client(*args, **kwargs)  # type: ignore
    Hub.current.bind_client(client)
    rv = _InitGuard(client)
    if client is not None:
        _initial_client = weakref.ref(client)
    return rv


MYPY = False
if MYPY:
    # Make mypy, PyCharm and other static analyzers think `init` is a type to
    # have nicer autocompletion for params.
    #
    # Use `ClientConstructor` to define the argument types of `init` and
    # `ContextManager[Any]` to tell static analyzers about the return type.

    class init(ClientConstructor, ContextManager[Any]):
        pass


else:
    # Alias `init` for actual usage. Go through the lambda indirection to throw
    # PyCharm off of the weakly typed signature (it would otherwise discover
    # both the weakly typed signature of `_init` and our faked `init` type).

    init = (lambda: _init)()


class HubMeta(type):
    @property
    def current(self):
        # type: () -> Hub
        """Returns the current instance of the hub."""
        rv = _local.get(None)
        if rv is None:
            rv = Hub(GLOBAL_HUB)
            _local.set(rv)
        return rv

    @property
    def main(self):
        # type: () -> Hub
        """Returns the main instance of the hub."""
        return GLOBAL_HUB


class _HubManager(object):
    def __init__(self, hub):
        # type: (Hub) -> None
        self._old = Hub.current
        _local.set(hub)

    def __exit__(self, exc_type, exc_value, tb):
        # type: (Any, Any, Any) -> None
        _local.set(self._old)


class _ScopeManager(object):
    def __init__(self, hub):
        # type: (Hub) -> None
        self._hub = hub
        self._original_len = len(hub._stack)
        self._layer = hub._stack[-1]

    def __enter__(self):
        # type: () -> Scope
        scope = self._layer[1]
        assert scope is not None
        return scope

    def __exit__(self, exc_type, exc_value, tb):
        # type: (Any, Any, Any) -> None
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


class Hub(with_metaclass(HubMeta)):  # type: ignore
    """The hub wraps the concurrency management of the SDK.  Each thread has
    its own hub but the hub might transfer with the flow of execution if
    context vars are available.

    If the hub is used with a with statement it's temporarily activated.
    """

    _stack = None  # type: List[Tuple[Optional[Client], Scope]]

    # Mypy doesn't pick up on the metaclass.

    if MYPY:
        current = None  # type: Hub
        main = None  # type: Hub

    def __init__(
        self,
        client_or_hub=None,  # type: Optional[Union[Hub, Client]]
        scope=None,  # type: Optional[Any]
    ):
        # type: (...) -> None
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
        self._last_event_id = None  # type: Optional[str]
        self._old_hubs = []  # type: List[Hub]

    def __enter__(self):
        # type: () -> Hub
        self._old_hubs.append(Hub.current)
        _local.set(self)
        return self

    def __exit__(
        self,
        exc_type,  # type: Optional[type]
        exc_value,  # type: Optional[BaseException]
        tb,  # type: Optional[Any]
    ):
        # type: (...) -> None
        old = self._old_hubs.pop()
        _local.set(old)

    def run(
        self, callback  # type: Callable[[], T]
    ):
        # type: (...) -> T
        """Runs a callback in the context of the hub.  Alternatively the
        with statement can be used on the hub directly.
        """
        with self:
            return callback()

    def get_integration(
        self, name_or_class  # type: Union[str, Type[Integration]]
    ):
        # type: (...) -> Any
        """Returns the integration for this hub by name or class.  If there
        is no client bound or the client does not have that integration
        then `None` is returned.

        If the return value is not `None` the hub is guaranteed to have a
        client attached.
        """
        if isinstance(name_or_class, str):
            integration_name = name_or_class
        elif name_or_class.identifier is not None:
            integration_name = name_or_class.identifier
        else:
            raise ValueError("Integration has no name")

        client = self._stack[-1][0]
        if client is not None:
            rv = client.integrations.get(integration_name)
            if rv is not None:
                return rv

        if _initial_client is not None:
            initial_client = _initial_client()
        else:
            initial_client = None

        if (
            initial_client is not None
            and initial_client is not client
            and initial_client.integrations.get(integration_name) is not None
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
        # type: () -> Optional[Client]
        """Returns the current client on the hub."""
        return self._stack[-1][0]

    def last_event_id(self):
        # type: () -> Optional[str]
        """Returns the last event ID."""
        return self._last_event_id

    def bind_client(
        self, new  # type: Optional[Client]
    ):
        # type: (...) -> None
        """Binds a new client to the hub."""
        top = self._stack[-1]
        self._stack[-1] = (new, top[1])

    def capture_event(
        self,
        event,  # type: Event
        hint=None,  # type: Optional[Hint]
    ):
        # type: (...) -> Optional[str]
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
        return None

    def capture_message(
        self,
        message,  # type: str
        level=None,  # type: Optional[str]
    ):
        # type: (...) -> Optional[str]
        """Captures a message.  The message is just a string.  If no level
        is provided the default level is `info`.
        """
        if self.client is None:
            return None
        if level is None:
            level = "info"
        return self.capture_event({"message": message, "level": level})

    def capture_exception(
        self, error=None  # type: Optional[BaseException]
    ):
        # type: (...) -> Optional[str]
        """Captures an exception.

        The argument passed can be `None` in which case the last exception
        will be reported, otherwise an exception object or an `exc_info`
        tuple.
        """
        client = self.client
        if client is None:
            return None
        if error is None:
            exc_info = sys.exc_info()
        else:
            exc_info = exc_info_from_error(error)

        event, hint = event_from_exception(exc_info, client_options=client.options)
        try:
            return self.capture_event(event, hint=hint)
        except Exception:
            self._capture_internal_exception(sys.exc_info())

        return None

    def _capture_internal_exception(
        self, exc_info  # type: _OptExcInfo
    ):
        # type: (...) -> Any
        """Capture an exception that is likely caused by a bug in the SDK
        itself."""
        logger.error("Internal error in sentry_sdk", exc_info=exc_info)  # type: ignore

    def add_breadcrumb(
        self,
        crumb=None,  # type: Optional[Breadcrumb]
        hint=None,  # type: Optional[BreadcrumbHint]
        **kwargs  # type: Any
    ):
        # type: (...) -> None
        """Adds a breadcrumb.  The breadcrumbs are a dictionary with the
        data as the sentry v7/v8 protocol expects.  `hint` is an optional
        value that can be used by `before_breadcrumb` to customize the
        breadcrumbs that are emitted.
        """
        client, scope = self._stack[-1]
        if client is None:
            logger.info("Dropped breadcrumb because no client bound")
            return

        crumb = dict(crumb or ())  # type: Breadcrumb
        crumb.update(kwargs)
        if not crumb:
            return

        hint = dict(hint or ())  # type: Hint

        if crumb.get("timestamp") is None:
            crumb["timestamp"] = datetime.utcnow()
        if crumb.get("type") is None:
            crumb["type"] = "default"

        if client.options["before_breadcrumb"] is not None:
            new_crumb = client.options["before_breadcrumb"](crumb, hint)
        else:
            new_crumb = crumb

        if new_crumb is not None:
            scope._breadcrumbs.append(new_crumb)
        else:
            logger.info("before breadcrumb dropped breadcrumb (%s)", crumb)

        max_breadcrumbs = client.options["max_breadcrumbs"]  # type: int
        while len(scope._breadcrumbs) > max_breadcrumbs:
            scope._breadcrumbs.popleft()

    @contextmanager
    def span(
        self,
        span=None,  # type: Optional[Span]
        **kwargs  # type: Any
    ):
        # type: (...) -> Generator[Span, None, None]
        span = self.start_span(span=span, **kwargs)

        _, scope = self._stack[-1]
        old_span = scope.span
        scope.span = span

        try:
            yield span
        except Exception:
            span.set_tag("error", True)
            raise
        else:
            span.set_tag("error", False)
        finally:
            try:
                span.finish()
                maybe_create_breadcrumbs_from_span(self, span)
                self.finish_span(span)
            except Exception:
                self._capture_internal_exception(sys.exc_info())
            scope.span = old_span

    def start_span(
        self,
        span=None,  # type: Optional[Span]
        **kwargs  # type: Any
    ):
        # type: (...) -> Span

        client, scope = self._stack[-1]

        if span is None:
            if scope.span is not None:
                span = scope.span.new_span(**kwargs)
            else:
                span = Span(**kwargs)

        if span.sampled is None and span.transaction is not None:
            sample_rate = client and client.options["traces_sample_rate"] or 0
            span.sampled = random.random() < sample_rate

        return span

    def finish_span(
        self, span  # type: Span
    ):
        # type: (...) -> Optional[str]
        if span.timestamp is None:
            # This transaction is not yet finished so we just finish it.
            span.finish()

        if span.transaction is None:
            # If this has no transaction set we assume there's a parent
            # transaction for this span that would be flushed out eventually.
            return None

        if self.client is None:
            # We have no client and therefore nowhere to send this transaction
            # event.
            return None

        if not span.sampled:
            # At this point a `sampled = None` should have already been
            # resolved to a concrete decision. If `sampled` is `None`, it's
            # likely that somebody used `with Hub.span(..)` on a
            # non-transaction span and later decided to make it a transaction.
            assert (
                span.sampled is not None
            ), "Need to set transaction when entering span!"
            return None

        return self.capture_event(
            {
                "type": "transaction",
                "transaction": span.transaction,
                "contexts": {"trace": span.get_trace_context()},
                "timestamp": span.timestamp,
                "start_timestamp": span.start_timestamp,
                "spans": [s.to_json() for s in span._finished_spans if s is not span],
            }
        )

    @overload  # noqa
    def push_scope(
        self, callback=None  # type: Optional[None]
    ):
        # type: (...) -> ContextManager[Scope]
        pass

    @overload  # noqa
    def push_scope(
        self, callback  # type: Callable[[Scope], None]
    ):
        # type: (...) -> None
        pass

    def push_scope(  # noqa
        self, callback=None  # type: Optional[Callable[[Scope], None]]
    ):
        # type: (...) -> Optional[ContextManager[Scope]]
        """Pushes a new layer on the scope stack. Returns a context manager
        that should be used to pop the scope again.  Alternatively a callback
        can be provided that is executed in the context of the scope.
        """

        if callback is not None:
            with self.push_scope() as scope:
                callback(scope)
            return None

        client, scope = self._stack[-1]
        new_layer = (client, copy.copy(scope))
        self._stack.append(new_layer)

        return _ScopeManager(self)

    scope = push_scope

    def pop_scope_unsafe(self):
        # type: () -> Tuple[Optional[Client], Scope]
        """Pops a scope layer from the stack. Try to use the context manager
        `push_scope()` instead."""
        rv = self._stack.pop()
        assert self._stack, "stack must have at least one layer"
        return rv

    @overload  # noqa
    def configure_scope(
        self, callback=None  # type: Optional[None]
    ):
        # type: (...) -> ContextManager[Scope]
        pass

    @overload  # noqa
    def configure_scope(
        self, callback  # type: Callable[[Scope], None]
    ):
        # type: (...) -> None
        pass

    def configure_scope(  # noqa
        self, callback=None  # type: Optional[Callable[[Scope], None]]
    ):  # noqa
        # type: (...) -> Optional[ContextManager[Scope]]

        """Reconfigures the scope."""

        client, scope = self._stack[-1]
        if callback is not None:
            if client is not None:
                callback(scope)

            return None

        @contextmanager
        def inner():
            # type: () -> Generator[Scope, None, None]
            if client is not None:
                yield scope
            else:
                yield Scope()

        return inner()

    def flush(
        self,
        timeout=None,  # type: Optional[float]
        callback=None,  # type: Optional[Callable[[int, float], None]]
    ):
        # type: (...) -> None
        """
        Alias for :py:meth:`sentry_sdk.Client.flush`
        """
        client, scope = self._stack[-1]
        if client is not None:
            return client.flush(timeout=timeout, callback=callback)

    def iter_trace_propagation_headers(self):
        # type: () -> Generator[Tuple[str, str], None, None]
        client, scope = self._stack[-1]
        if scope._span is None:
            return

        propagate_traces = client and client.options["propagate_traces"]
        if not propagate_traces:
            return

        if client and client.options["traceparent_v2"]:
            traceparent = scope._span.to_traceparent()
        else:
            traceparent = scope._span.to_legacy_traceparent()

        yield "sentry-trace", traceparent


GLOBAL_HUB = Hub()
_local.set(GLOBAL_HUB)
