from __future__ import annotations
import inspect
from contextlib import contextmanager

from sentry_sdk import tracing_utils, Client
from sentry_sdk._init_implementation import init
from sentry_sdk.tracing import trace
from sentry_sdk.crons import monitor

# TODO-neel-potel make 2 scope strategies/impls and switch
from sentry_sdk.scope import Scope as BaseScope
from sentry_sdk.opentelemetry.scope import (
    PotelScope as Scope,
    new_scope,
    isolation_scope,
    use_scope,
    use_isolation_scope,
)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, Callable, TypeVar, Union, Generator

    T = TypeVar("T")
    F = TypeVar("F", bound=Callable[..., Any])

    from collections.abc import Mapping
    from sentry_sdk.client import BaseClient
    from sentry_sdk.tracing import Span
    from sentry_sdk._types import (
        Event,
        Hint,
        LogLevelStr,
        ExcInfo,
        BreadcrumbHint,
        Breadcrumb,
    )


# When changing this, update __all__ in __init__.py too
__all__ = [
    "init",
    "add_attachment",
    "add_breadcrumb",
    "capture_event",
    "capture_exception",
    "capture_message",
    "continue_trace",
    "new_trace",
    "flush",
    "get_baggage",
    "get_client",
    "get_global_scope",
    "get_isolation_scope",
    "get_current_scope",
    "get_current_span",
    "get_traceparent",
    "is_initialized",
    "isolation_scope",
    "last_event_id",
    "new_scope",
    "set_context",
    "set_extra",
    "set_level",
    "set_tag",
    "set_tags",
    "set_user",
    "start_span",
    "start_transaction",
    "trace",
    "monitor",
    "use_scope",
    "use_isolation_scope",
    "start_session",
    "end_session",
    "set_transaction_name",
    "update_current_span",
]


def scopemethod(f: F) -> F:
    f.__doc__ = "%s\n\n%s" % (
        "Alias for :py:meth:`sentry_sdk.Scope.%s`" % f.__name__,
        inspect.getdoc(getattr(Scope, f.__name__)),
    )
    return f


def clientmethod(f: F) -> F:
    f.__doc__ = "%s\n\n%s" % (
        "Alias for :py:meth:`sentry_sdk.Client.%s`" % f.__name__,
        inspect.getdoc(getattr(Client, f.__name__)),
    )
    return f


@scopemethod
def get_client() -> BaseClient:
    return Scope.get_client()


def is_initialized() -> bool:
    """
    .. versionadded:: 2.0.0

    Returns whether Sentry has been initialized or not.

    If a client is available and the client is active
    (meaning it is configured to send data) then
    Sentry is initialized.
    """
    return get_client().is_active()


@scopemethod
def get_global_scope() -> BaseScope:
    return Scope.get_global_scope()


@scopemethod
def get_isolation_scope() -> Scope:
    return Scope.get_isolation_scope()


@scopemethod
def get_current_scope() -> Scope:
    return Scope.get_current_scope()


@scopemethod
def last_event_id() -> Optional[str]:
    """
    See :py:meth:`sentry_sdk.Scope.last_event_id` documentation regarding
    this method's limitations.
    """
    return Scope.last_event_id()


@scopemethod
def capture_event(
    event: Event,
    hint: Optional[Hint] = None,
    scope: Optional[Any] = None,
    **scope_kwargs: Any,
) -> Optional[str]:
    return get_current_scope().capture_event(event, hint, scope=scope, **scope_kwargs)


@scopemethod
def capture_message(
    message: str,
    level: Optional[LogLevelStr] = None,
    scope: Optional[Any] = None,
    **scope_kwargs: Any,
) -> Optional[str]:
    return get_current_scope().capture_message(
        message, level, scope=scope, **scope_kwargs
    )


@scopemethod
def capture_exception(
    error: Optional[Union[BaseException, ExcInfo]] = None,
    scope: Optional[Any] = None,
    **scope_kwargs: Any,
) -> Optional[str]:
    return get_current_scope().capture_exception(error, scope=scope, **scope_kwargs)


@scopemethod
def add_attachment(
    bytes: Union[None, bytes, Callable[[], bytes]] = None,
    filename: Optional[str] = None,
    path: Optional[str] = None,
    content_type: Optional[str] = None,
    add_to_transactions: bool = False,
) -> None:
    return get_isolation_scope().add_attachment(
        bytes, filename, path, content_type, add_to_transactions
    )


@scopemethod
def add_breadcrumb(
    crumb: Optional[Breadcrumb] = None,
    hint: Optional[BreadcrumbHint] = None,
    **kwargs: Any,
) -> None:
    return get_isolation_scope().add_breadcrumb(crumb, hint, **kwargs)


@scopemethod
def set_tag(key: str, value: Any) -> None:
    return get_isolation_scope().set_tag(key, value)


@scopemethod
def set_tags(tags: Mapping[str, object]) -> None:
    return get_isolation_scope().set_tags(tags)


@scopemethod
def set_context(key: str, value: dict[str, Any]) -> None:
    return get_isolation_scope().set_context(key, value)


@scopemethod
def set_extra(key: str, value: Any) -> None:
    return get_isolation_scope().set_extra(key, value)


@scopemethod
def set_user(value: Optional[dict[str, Any]]) -> None:
    return get_isolation_scope().set_user(value)


@scopemethod
def set_level(value: LogLevelStr) -> None:
    return get_isolation_scope().set_level(value)


@clientmethod
def flush(
    timeout: Optional[float] = None,
    callback: Optional[Callable[[int, float], None]] = None,
) -> None:
    return get_client().flush(timeout=timeout, callback=callback)


def start_span(**kwargs: Any) -> Span:
    """
    Start and return a span.

    This is the entry point to manual tracing instrumentation.

    A tree structure can be built by adding child spans to the span.
    To start a new child span within the span, call the `start_child()` method.

    When used as a context manager, spans are automatically finished at the end
    of the `with` block. If not using context managers, call the `finish()`
    method.
    """
    return get_current_scope().start_span(**kwargs)


def start_transaction(transaction: Optional[Span] = None, **kwargs: Any) -> Span:
    """
    .. deprecated:: 3.0.0
        This function is deprecated and will be removed in a future release.
        Use :py:meth:`sentry_sdk.start_span` instead.

    Start and return a transaction on the current scope.

    Start an existing transaction if given, otherwise create and start a new
    transaction with kwargs.

    This is the entry point to manual tracing instrumentation.

    A tree structure can be built by adding child spans to the transaction,
    and child spans to other spans. To start a new child span within the
    transaction or any span, call the respective `.start_child()` method.

    Every child span must be finished before the transaction is finished,
    otherwise the unfinished spans are discarded.

    When used as context managers, spans and transactions are automatically
    finished at the end of the `with` block. If not using context managers,
    call the `.finish()` method.

    When the transaction is finished, it will be sent to Sentry with all its
    finished child spans.

    :param transaction: The transaction to start. If omitted, we create and
        start a new transaction.
    :param kwargs: Optional keyword arguments to be passed to the Transaction
        constructor. See :py:class:`sentry_sdk.tracing.Transaction` for
        available arguments.
    """
    return start_span(
        span=transaction,
        **kwargs,
    )


def get_current_span(scope: Optional[Scope] = None) -> Optional[Span]:
    """
    Returns the currently active span if there is one running, otherwise `None`
    """
    return tracing_utils.get_current_span(scope)


def get_traceparent() -> Optional[str]:
    """
    Returns the traceparent either from the active span or from the scope.
    """
    return get_current_scope().get_traceparent()


def get_baggage() -> Optional[str]:
    """
    Returns Baggage either from the active span or from the scope.
    """
    baggage = get_current_scope().get_baggage()
    if baggage is not None:
        return baggage.serialize()

    return None


@contextmanager
def continue_trace(environ_or_headers: dict[str, Any]) -> Generator[None, None, None]:
    """
    Sets the propagation context from environment or headers to continue an incoming trace.
    """
    with get_isolation_scope().continue_trace(environ_or_headers):
        yield


@contextmanager
def new_trace() -> Generator[None, None, None]:
    """
    Force creation of a new trace.
    """
    with get_isolation_scope().new_trace():
        yield


@scopemethod
def start_session(
    session_mode: str = "application",
) -> None:
    return get_isolation_scope().start_session(session_mode=session_mode)


@scopemethod
def end_session() -> None:
    return get_isolation_scope().end_session()


@scopemethod
def set_transaction_name(name: str, source: Optional[str] = None) -> None:
    return get_current_scope().set_transaction_name(name, source)


def update_current_span(op=None, name=None, attributes=None):
    # type: (Optional[str], Optional[str], Optional[dict[str, Union[str, int, float, bool]]]) -> None
    """
    Update the current active span with the provided parameters.

    This function allows you to modify properties of the currently active span.
    If no span is currently active, this function will do nothing.

    :param op: The operation name for the span. This is a high-level description
        of what the span represents (e.g., "http.client", "db.query").
        You can use predefined constants from :py:class:`sentry_sdk.consts.OP`
        or provide your own string. If not provided, the span's operation will
        remain unchanged.
    :type op: str or None

    :param name: The human-readable name/description for the span. This provides
        more specific details about what the span represents (e.g., "GET /api/users",
        "SELECT * FROM users"). If not provided, the span's name will remain unchanged.
    :type name: str or None

    :param attributes: A dictionary of key-value pairs to add as attributes to the span.
        Attribute values must be strings, integers, floats, or booleans. These
        attributes will be merged with any existing span data. If not provided,
        no attributes will be added.
    :type attributes: dict[str, Union[str, int, float, bool]] or None

    :returns: None

    .. versionadded:: 2.35.0

    Example::

        import sentry_sdk
        from sentry_sdk.consts import OP

        sentry_sdk.update_current_span(
            op=OP.FUNCTION,
            name="process_user_data",
            attributes={"user_id": 123, "batch_size": 50}
        )
    """
    current_span = get_current_span()

    if current_span is None:
        return

    if op is not None:
        current_span.op = op

    if name is not None:
        # internally it is still description
        current_span.description = name

    if attributes is not None:
        current_span.set_attributes(attributes)
