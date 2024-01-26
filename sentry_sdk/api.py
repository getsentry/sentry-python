import inspect

from sentry_sdk import tracing_utils
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.hub import Hub
from sentry_sdk.scope import Scope
from sentry_sdk.tracing import NoOpSpan, Transaction

if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import Optional
    from typing import overload
    from typing import Callable
    from typing import TypeVar
    from typing import ContextManager
    from typing import Union

    from sentry_sdk._types import (
        Event,
        Hint,
        Breadcrumb,
        BreadcrumbHint,
        ExcInfo,
        MeasurementUnit,
    )
    from sentry_sdk.tracing import Span

    T = TypeVar("T")
    F = TypeVar("F", bound=Callable[..., Any])
else:

    def overload(x: T) -> T:
        return x


# When changing this, update __all__ in __init__.py too
__all__ = [
    "capture_event",
    "capture_message",
    "capture_exception",
    "add_breadcrumb",
    "configure_scope",
    "push_scope",
    "flush",
    "last_event_id",
    "start_span",
    "start_transaction",
    "set_tag",
    "set_context",
    "set_extra",
    "set_user",
    "set_level",
    "set_measurement",
    "get_current_span",
    "get_traceparent",
    "get_baggage",
    "continue_trace",
]


def hubmethod(f: F) -> F:
    f.__doc__ = "%s\n\n%s" % (
        "Alias for :py:meth:`sentry_sdk.Hub.%s`" % f.__name__,
        inspect.getdoc(getattr(Hub, f.__name__)),
    )
    return f


def scopemethod(f: F) -> F:
    f.__doc__ = "%s\n\n%s" % (
        "Alias for :py:meth:`sentry_sdk.Scope.%s`" % f.__name__,
        inspect.getdoc(getattr(Scope, f.__name__)),
    )
    return f


@hubmethod
def capture_event(
    event: Event,
    hint: Optional[Hint] = None,
    scope: Optional[Any] = None,
    **scope_kwargs: Any
) -> Optional[str]:
    return Hub.current.capture_event(event, hint, scope=scope, **scope_kwargs)


@hubmethod
def capture_message(
    message: str,
    level: Optional[str] = None,
    scope: Optional[Any] = None,
    **scope_kwargs: Any
) -> Optional[str]:
    return Hub.current.capture_message(message, level, scope=scope, **scope_kwargs)


@hubmethod
def capture_exception(
    error: Optional[Union[BaseException, ExcInfo]] = None,
    scope: Optional[Any] = None,
    **scope_kwargs: Any
) -> Optional[str]:
    return Hub.current.capture_exception(error, scope=scope, **scope_kwargs)


@hubmethod
def add_breadcrumb(
    crumb: Optional[Breadcrumb] = None,
    hint: Optional[BreadcrumbHint] = None,
    **kwargs: Any
) -> None:
    return Hub.current.add_breadcrumb(crumb, hint, **kwargs)


@overload
def configure_scope() -> ContextManager[Scope]:
    pass


@overload
def configure_scope(  # noqa: F811
    callback: Callable[[Scope], None],
) -> None:
    pass


@hubmethod
def configure_scope(  # noqa: F811
    callback: Optional[Callable[[Scope], None]] = None,
) -> Optional[ContextManager[Scope]]:
    return Hub.current.configure_scope(callback)


@overload
def push_scope() -> ContextManager[Scope]:
    pass


@overload
def push_scope(  # noqa: F811
    callback: Callable[[Scope], None],
) -> None:
    pass


@hubmethod
def push_scope(  # noqa: F811
    callback: Optional[Callable[[Scope], None]] = None,
) -> Optional[ContextManager[Scope]]:
    return Hub.current.push_scope(callback)


@scopemethod
def set_tag(key: str, value: Any) -> None:
    return Hub.current.scope.set_tag(key, value)


@scopemethod
def set_context(key: str, value: Dict[str, Any]) -> None:
    return Hub.current.scope.set_context(key, value)


@scopemethod
def set_extra(key: str, value: Any) -> None:
    return Hub.current.scope.set_extra(key, value)


@scopemethod
def set_user(value: Optional[Dict[str, Any]]) -> None:
    return Hub.current.scope.set_user(value)


@scopemethod
def set_level(value: str) -> None:
    return Hub.current.scope.set_level(value)


@hubmethod
def flush(
    timeout: Optional[float] = None,
    callback: Optional[Callable[[int, float], None]] = None,
) -> None:
    return Hub.current.flush(timeout=timeout, callback=callback)


@hubmethod
def last_event_id() -> Optional[str]:
    return Hub.current.last_event_id()


@hubmethod
def start_span(span: Optional[Span] = None, **kwargs: Any) -> Span:
    return Hub.current.start_span(span=span, **kwargs)


@hubmethod
def start_transaction(
    transaction: Optional[Transaction] = None, **kwargs: Any
) -> Union[Transaction, NoOpSpan]:
    return Hub.current.start_transaction(transaction, **kwargs)


def set_measurement(name: str, value: float, unit: MeasurementUnit = "") -> None:
    transaction = Hub.current.scope.transaction
    if transaction is not None:
        transaction.set_measurement(name, value, unit)


def get_current_span(hub: Optional[Hub] = None) -> Optional[Span]:
    """
    Returns the currently active span if there is one running, otherwise `None`
    """
    return tracing_utils.get_current_span(hub)


def get_traceparent() -> Optional[str]:
    """
    Returns the traceparent either from the active span or from the scope.
    """
    return Hub.current.get_traceparent()


def get_baggage() -> Optional[str]:
    """
    Returns Baggage either from the active span or from the scope.
    """
    return Hub.current.get_baggage()


def continue_trace(
    environ_or_headers: Dict[str, Any],
    op: Optional[str] = None,
    name: Optional[str] = None,
    source: Optional[str] = None,
) -> Transaction:
    """
    Sets the propagation context from environment or headers and returns a transaction.
    """
    return Hub.current.continue_trace(environ_or_headers, op, name, source)
