"""
The API in this file is only meant to be used in span streaming mode.

You can enable span streaming mode via
sentry_sdk.init(_experiments={"trace_lifecycle": "stream"}).
"""

import uuid
from enum import Enum
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.tracing_utils import Baggage
from sentry_sdk.utils import format_attribute, logger

if TYPE_CHECKING:
    from typing import Any, Callable, Optional, ParamSpec, TypeVar, Union
    from sentry_sdk._types import Attributes, AttributeValue

    P = ParamSpec("P")
    R = TypeVar("R")


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


# Segment source, see
# https://getsentry.github.io/sentry-conventions/generated/attributes/sentry.html#sentryspansource
class SegmentSource(str, Enum):
    COMPONENT = "component"
    CUSTOM = "custom"
    ROUTE = "route"
    TASK = "task"
    URL = "url"
    VIEW = "view"

    def __str__(self) -> str:
        return self.value


# These are typically high cardinality and the server hates them
LOW_QUALITY_SEGMENT_SOURCES = [
    SegmentSource.URL,
]


SOURCE_FOR_STYLE = {
    "endpoint": SegmentSource.COMPONENT,
    "function_name": SegmentSource.COMPONENT,
    "handler_name": SegmentSource.COMPONENT,
    "method_and_path_pattern": SegmentSource.ROUTE,
    "path": SegmentSource.URL,
    "route_name": SegmentSource.COMPONENT,
    "route_pattern": SegmentSource.ROUTE,
    "uri_template": SegmentSource.ROUTE,
    "url": SegmentSource.ROUTE,
}


# Sentinel value for an unset parent_span to be able to distinguish it from
# a None set by the user
_DEFAULT_PARENT_SPAN = object()


def start_span(
    name: str,
    attributes: "Optional[Attributes]" = None,
    parent_span: "Optional[StreamedSpan]" = _DEFAULT_PARENT_SPAN,  # type: ignore[assignment]
    active: bool = True,
) -> "StreamedSpan":
    """
    Start a span.

    The span's parent, unless provided explicitly via the `parent_span` argument,
    will be the current active span, if any. If there is none, this span will
    become the root of a new span tree. If you explicitly want this span to be
    top-level without a parent, set `parent_span=None`.

    `start_span()` can either be used as context manager or you can use the span
    object it returns and explicitly end it via `span.end()`. The following is
    equivalent:

    ```python
    import sentry_sdk

    with sentry_sdk.traces.start_span(name="My Span"):
        # do something

    # The span automatically finishes once the `with` block is exited
    ```

    ```python
    import sentry_sdk

    span = sentry_sdk.traces.start_span(name="My Span")
    # do something
    span.end()
    ```

    To continue a trace from another service, call
    `sentry_sdk.traces.continue_trace()` prior to creating a top-level span.

    :param name: The name to identify this span by.
    :type name: str

    :param attributes: Key-value attributes to set on the span from the start.
        These will also be accessible in the traces sampler.
    :type attributes: "Optional[Attributes]"

    :param parent_span: A span instance that the new span should consider its
        parent. If not provided, the parent will be set to the currently active
        span, if any. If set to `None`, this span will become a new root-level
        span.
    :type parent_span: "Optional[StreamedSpan]"

    :param active: Controls whether spans started while this span is running
        will automatically become its children. That's the default behavior. If
        you want to create a span that shouldn't have any children (unless
        provided explicitly via the `parent_span` argument), set this to `False`.
    :type active: bool

    :return: The span that has been started.
    :rtype: StreamedSpan
    """
    return sentry_sdk.get_current_scope().start_streamed_span(
        name, attributes, parent_span, active
    )


def continue_trace(incoming: "dict[str, Any]") -> None:
    """
    Continue a trace from headers or environment variables.

    This function sets the propagation context on the scope. Any span started
    in the updated scope will belong under the trace extracted from the
    provided propagation headers or environment variables.

    continue_trace() doesn't start any spans on its own. Use the start_span()
    API for that.
    """
    # This is set both on the isolation and the current scope for compatibility
    # reasons. Conceptually, it belongs on the isolation scope, and it also
    # used to be set there in non-span-first mode. But in span first mode, we
    # start spans on the current scope, regardless of type, like JS does, so we
    # need to set the propagation context there.
    sentry_sdk.get_isolation_scope().generate_propagation_context(
        incoming,
    )
    sentry_sdk.get_current_scope().generate_propagation_context(
        incoming,
    )


def new_trace() -> None:
    """
    Resets the propagation context, forcing a new trace.

    This function sets the propagation context on the scope. Any span started
    in the updated scope will start its own trace.

    new_trace() doesn't start any spans on its own. Use the start_span() API
    for that.
    """
    sentry_sdk.get_isolation_scope().set_new_propagation_context()
    sentry_sdk.get_current_scope().set_new_propagation_context()


class StreamedSpan:
    """
    A span holds timing information of a block of code.

    Spans can have multiple child spans, thus forming a span tree.

    This is the Span First span implementation that streams spans. The original
    transaction-based span implementation lives in tracing.Span.
    """

    __slots__ = (
        "_name",
        "_attributes",
        "_active",
        "_span_id",
        "_trace_id",
        "_parent_span_id",
        "_segment",
        "_parent_sampled",
        "_status",
        "_scope",
        "_baggage",
    )

    def __init__(
        self,
        *,
        name: str,
        attributes: "Optional[Attributes]" = None,
        active: bool = True,
        scope: "sentry_sdk.Scope",
        segment: "Optional[StreamedSpan]" = None,
        trace_id: "Optional[str]" = None,
        parent_span_id: "Optional[str]" = None,
        parent_sampled: "Optional[bool]" = None,
        baggage: "Optional[Baggage]" = None,
    ):
        self._name: str = name
        self._active: bool = active
        self._attributes: "Attributes" = {}
        if attributes:
            for attribute, value in attributes.items():
                self.set_attribute(attribute, value)

        self._scope = scope

        self._segment = segment or self

        self._trace_id: "Optional[str]" = trace_id
        self._parent_span_id = parent_span_id
        self._parent_sampled = parent_sampled
        self._baggage = baggage

        self._span_id: "Optional[str]" = None

        self._status = SpanStatus.OK.value
        self.set_attribute("sentry.span.source", SegmentSource.CUSTOM.value)

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"name={self._name}, "
            f"trace_id={self.trace_id}, "
            f"span_id={self.span_id}, "
            f"parent_span_id={self._parent_span_id}, "
            f"active={self._active})>"
        )

    def __enter__(self) -> "StreamedSpan":
        return self

    def __exit__(
        self, ty: "Optional[Any]", value: "Optional[Any]", tb: "Optional[Any]"
    ) -> None:
        pass

    def get_attributes(self) -> "Attributes":
        return self._attributes

    def set_attribute(self, key: str, value: "AttributeValue") -> None:
        self._attributes[key] = format_attribute(value)

    def set_attributes(self, attributes: "Attributes") -> None:
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def remove_attribute(self, key: str) -> None:
        try:
            del self._attributes[key]
        except KeyError:
            pass

    @property
    def status(self) -> "str":
        return self._status

    @status.setter
    def status(self, status: "Union[SpanStatus, str]") -> None:
        if isinstance(status, Enum):
            status = status.value

        if status not in {e.value for e in SpanStatus}:
            logger.debug(
                f'Unsupported span status {status}. Expected one of: "ok", "error"'
            )
            return

        self._status = status

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        self._name = name

    @property
    def active(self) -> bool:
        return self._active

    @property
    def span_id(self) -> str:
        if not self._span_id:
            self._span_id = uuid.uuid4().hex[16:]

        return self._span_id

    @property
    def trace_id(self) -> str:
        if not self._trace_id:
            self._trace_id = uuid.uuid4().hex

        return self._trace_id

    @property
    def sampled(self) -> "Optional[bool]":
        return True


class NoOpStreamedSpan(StreamedSpan):
    def __init__(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(sampled={self.sampled})>"

    def __enter__(self) -> "NoOpStreamedSpan":
        return self

    def __exit__(
        self, ty: "Optional[Any]", value: "Optional[Any]", tb: "Optional[Any]"
    ) -> None:
        pass

    def get_attributes(self) -> "Attributes":
        return {}

    def set_attribute(self, key: str, value: "AttributeValue") -> None:
        pass

    def set_attributes(self, attributes: "Attributes") -> None:
        pass

    def remove_attribute(self, key: str) -> None:
        pass

    @property
    def status(self) -> "str":
        return SpanStatus.OK.value

    @status.setter
    def status(self, status: "Union[SpanStatus, str]") -> None:
        pass

    @property
    def name(self) -> str:
        return ""

    @name.setter
    def name(self, value: str) -> None:
        pass

    @property
    def active(self) -> bool:
        return True

    @property
    def span_id(self) -> str:
        return "0000000000000000"

    @property
    def trace_id(self) -> str:
        return "00000000000000000000000000000000"

    @property
    def sampled(self) -> "Optional[bool]":
        return False


def trace(
    func: "Optional[Callable[P, R]]" = None,
    *,
    name: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
    active: bool = True,
) -> "Union[Callable[P, R], Callable[[Callable[P, R]], Callable[P, R]]]":
    """
    Decorator to start a span around a function call.

    This decorator automatically creates a new span when the decorated function
    is called, and finishes the span when the function returns or raises an exception.

    :param func: The function to trace. When used as a decorator without parentheses,
        this is the function being decorated. When used with parameters (e.g.,
        ``@trace(op="custom")``, this should be None.
    :type func: Callable or None

    :param name: The human-readable name/description for the span. If not provided,
        defaults to the function name. This provides more specific details about
        what the span represents (e.g., "GET /api/users", "process_user_data").
    :type name: str or None

    :param attributes: A dictionary of key-value pairs to add as attributes to the span.
        Attribute values must be strings, integers, floats, or booleans. These
        attributes provide additional context about the span's execution.
    :type attributes: dict[str, Any] or None

    :param active: Controls whether spans started while this span is running
        will automatically become its children. That's the default behavior. If
        you want to create a span that shouldn't have any children (unless
        provided explicitly via the `parent_span` argument), set this to False.
    :type active: bool

    :returns: When used as ``@trace``, returns the decorated function. When used as
        ``@trace(...)`` with parameters, returns a decorator function.
    :rtype: Callable or decorator function

    Example::

        import sentry_sdk

        # Simple usage with default values
        @sentry_sdk.trace
        def process_data():
            # Function implementation
            pass

        # With custom parameters
        @sentry_sdk.trace(
            name="Get user data",
            attributes={"postgres": True}
        )
        def make_db_query(sql):
            # Function implementation
            pass
    """
    from sentry_sdk.tracing_utils import create_streaming_span_decorator

    decorator = create_streaming_span_decorator(
        name=name,
        attributes=attributes,
        active=active,
    )

    if func:
        return decorator(func)
    else:
        return decorator
