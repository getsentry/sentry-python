import inspect
from typing import TYPE_CHECKING

from sentry_sdk import Client, traces
from sentry_sdk._init_implementation import init
from sentry_sdk.crons import monitor
from sentry_sdk.scope import Scope, isolation_scope, new_scope

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import (
        Any,
        Callable,
        Dict,
        Optional,
        TypeVar,
        Union,
        overload,
    )

    from sentry_sdk._types import (
        Breadcrumb,
        BreadcrumbHint,
        Event,
        ExcInfo,
        Hint,
        LogLevelStr,
    )
    from sentry_sdk.client import BaseClient
    from sentry_sdk.traces import StreamedSpan

    T = TypeVar("T")
    F = TypeVar("F", bound=Callable[..., Any])
else:

    def overload(x: "T") -> "T":
        return x


# When changing this, update __all__ in __init__.py too
__all__ = [
    "init",
    "add_attachment",
    "add_breadcrumb",
    "capture_event",
    "capture_exception",
    "capture_message",
    "continue_trace",
    "flush",
    "flush_async",
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
    "remove_attribute",
    "set_attribute",
    "set_attributes",
    "set_context",
    "set_extra",
    "set_level",
    "set_tag",
    "set_tags",
    "set_user",
    "start_span",
    "monitor",
    "start_session",
    "end_session",
    "set_transaction_name",
]


def scopemethod(f: "F") -> "F":
    f.__doc__ = "%s\n\n%s" % (
        "Alias for :py:meth:`sentry_sdk.Scope.%s`" % f.__name__,
        inspect.getdoc(getattr(Scope, f.__name__)),
    )
    return f


def clientmethod(f: "F") -> "F":
    f.__doc__ = "%s\n\n%s" % (
        "Alias for :py:meth:`sentry_sdk.Client.%s`" % f.__name__,
        inspect.getdoc(getattr(Client, f.__name__)),
    )
    return f


@scopemethod
def get_client() -> "BaseClient":
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
def get_global_scope() -> "Scope":
    return Scope.get_global_scope()


@scopemethod
def get_isolation_scope() -> "Scope":
    return Scope.get_isolation_scope()


@scopemethod
def get_current_scope() -> "Scope":
    return Scope.get_current_scope()


@scopemethod
def last_event_id() -> "Optional[str]":
    """
    See :py:meth:`sentry_sdk.Scope.last_event_id` documentation regarding
    this method's limitations.
    """
    return Scope.last_event_id()


@scopemethod
def capture_event(
    event: "Event",
    hint: "Optional[Hint]" = None,
    scope: "Optional[Any]" = None,
    **scope_kwargs: "Any",
) -> "Optional[str]":
    return get_current_scope().capture_event(event, hint, scope=scope, **scope_kwargs)


@scopemethod
def capture_message(
    message: str,
    level: "Optional[LogLevelStr]" = None,
    scope: "Optional[Any]" = None,
    **scope_kwargs: "Any",
) -> "Optional[str]":
    return get_current_scope().capture_message(
        message, level, scope=scope, **scope_kwargs
    )


@scopemethod
def capture_exception(
    error: "Optional[Union[BaseException, ExcInfo]]" = None,
    scope: "Optional[Any]" = None,
    **scope_kwargs: "Any",
) -> "Optional[str]":
    return get_current_scope().capture_exception(error, scope=scope, **scope_kwargs)


@scopemethod
def add_attachment(
    bytes: "Union[None, bytes, Callable[[], bytes]]" = None,
    filename: "Optional[str]" = None,
    path: "Optional[str]" = None,
    content_type: "Optional[str]" = None,
    add_to_transactions: bool = False,
) -> None:
    return get_isolation_scope().add_attachment(
        bytes, filename, path, content_type, add_to_transactions
    )


@scopemethod
def add_breadcrumb(
    crumb: "Optional[Breadcrumb]" = None,
    hint: "Optional[BreadcrumbHint]" = None,
    **kwargs: "Any",
) -> None:
    return get_isolation_scope().add_breadcrumb(crumb, hint, **kwargs)


@scopemethod
def set_attribute(attribute: str, value: "Any") -> None:
    """
    Set an attribute.

    Any attributes-based telemetry (logs, metrics, streamed spans) captured in
    this scope will include this attribute.
    """
    return get_isolation_scope().set_attribute(attribute, value)


@scopemethod
def set_attributes(attributes: "dict[str, Any]") -> None:
    """
    Set multiple attributes.

    Any attributes-based telemetry (logs, metrics, streamed spans) captured in
    this scope will include these attributes.
    """
    return get_isolation_scope().set_attributes(attributes)


@scopemethod
def remove_attribute(attribute: str) -> None:
    """
    Remove an attribute.

    If the attribute doesn't exist, this function will not have any effect and
    it will also not raise an exception.
    """
    return get_isolation_scope().remove_attribute(attribute)


@scopemethod
def set_tag(key: str, value: "Any") -> None:
    return get_isolation_scope().set_tag(key, value)


@scopemethod
def set_tags(tags: "Mapping[str, object]") -> None:
    return get_isolation_scope().set_tags(tags)


@scopemethod
def set_context(key: str, value: "Dict[str, Any]") -> None:
    return get_isolation_scope().set_context(key, value)


@scopemethod
def set_extra(key: str, value: "Any") -> None:
    return get_isolation_scope().set_extra(key, value)


@scopemethod
def set_user(value: "Optional[Dict[str, Any]]") -> None:
    return get_isolation_scope().set_user(value)


@scopemethod
def set_level(value: "LogLevelStr") -> None:
    return get_isolation_scope().set_level(value)


@clientmethod
def flush(
    timeout: "Optional[float]" = None,
    callback: "Optional[Callable[[int, float], None]]" = None,
) -> None:
    return get_client().flush(timeout=timeout, callback=callback)


@clientmethod
async def flush_async(
    timeout: "Optional[float]" = None,
    callback: "Optional[Callable[[int, float], None]]" = None,
) -> None:
    return await get_client().flush_async(timeout=timeout, callback=callback)


@scopemethod
def start_span(
    **kwargs: "Any",
) -> "StreamedSpan":
    return traces.start_span(**kwargs)


def get_current_span(
    scope: "Optional[Scope]" = None,
) -> "Optional[StreamedSpan]":
    """
    Returns the currently active span if there is one running, otherwise `None`
    """
    return traces.get_current_span(scope)


def get_traceparent() -> "Optional[str]":
    """
    Returns the traceparent either from the active span or from the scope.
    """
    return get_current_scope().get_traceparent()


def get_baggage() -> "Optional[str]":
    """
    Returns Baggage either from the active span or from the scope.
    """
    baggage = get_current_scope().get_baggage()
    if baggage is not None:
        return baggage.serialize()

    return None


def continue_trace(incoming: "Dict[str, Any]") -> None:
    """
    Sets the propagation context from environment or headers and returns a transaction.
    """
    return traces.continue_trace(incoming)


@scopemethod
def start_session(
    session_mode: str = "application",
) -> None:
    return get_isolation_scope().start_session(session_mode=session_mode)


@scopemethod
def end_session() -> None:
    return get_isolation_scope().end_session()


@scopemethod
def set_transaction_name(name: str, source: "Optional[str]" = None) -> None:
    return get_current_scope().set_transaction_name(name, source)
