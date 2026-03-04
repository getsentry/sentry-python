"""
The API in this file is only meant to be used in span streaming mode.

You can enable span streaming mode via
sentry_sdk.init(_experiments={"trace_lifecycle": "stream"}).
"""

import uuid
import warnings
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.profiler.continuous_profiler import get_profiler_id
from sentry_sdk.tracing_utils import (
    Baggage,
    has_tracing_enabled,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    format_attribute,
    get_current_thread_meta,
    logger,
    nanosecond_time,
    should_be_treated_as_error,
)

if TYPE_CHECKING:
    from typing import Any, Callable, Iterator, Optional, ParamSpec, TypeVar, Union
    from sentry_sdk._types import Attributes, AttributeValue, SamplingContext
    from sentry_sdk.profiler.continuous_profiler import ContinuousProfile

    P = ParamSpec("P")
    R = TypeVar("R")

BAGGAGE_HEADER_NAME = "baggage"
SENTRY_TRACE_HEADER_NAME = "sentry-trace"


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

"""
TODO[span-first] / notes
- tags
- dropped spans are not migrated
- recheck transaction.finish <-> Streamedspan.end
- profiling: drop transaction based
- profiling: actually send profiles
- maybe: use getters/setter OR properties but not both
- add size-based flushing to buffer(s)
- migrate transaction sample_rand logic
- check where we're auto filtering out spans in integrations (health checks etc?)

Notes:
- removed ability to provide a start_timestamp
"""


def start_span(
    name: str,
    attributes: "Optional[Attributes]" = None,
    parent_span: "Optional[StreamedSpan]" = None,
    active: bool = True,
) -> "StreamedSpan":
    """
    Start a span.

    The span's parent, unless provided explicitly via the `parent_span` argument,
    will be the currently active span, if any.

    `start_span()` can either be used as context manager or you can use the span
    object it returns and explicitly start and end it via the `span.start()` and
    `span.end()` interface. The following is equivalent:

    ```python
    import sentry_sdk

    with sentry_sdk.traces.start_span(name="My Span"):
        # do something

    # The span automatically finishes once the `with` block is exited
    ```

    ```python
    import sentry_sdk

    span = sentry_sdk.traces.start_span(name="My Span")
    span.start()
    # do something
    span.end()
    ```

    To continue a trace from another service, call
    sentry_sdk.traces.continue_trace() prior to creating the top-level span.

    :param name: The name to identify this span by.
    :type name: str

    :param attributes: Key-value attributes to set on the span from the start.
        When provided via the `start_span()` function, these will also be
        accessible in the traces sampler.
    :type attributes: "Optional[Attributes]"

    :param parent_span: A span instance that the new span should be parented to.
        If not provided, the parent will be set to the currently active span,
        if any.
    :type parent_span: "Optional[StreamedSpan]"

    :param active: Controls whether spans started while this span is running
        will automatically become its children. That's the default behavior. If
        you want to create a span that shouldn't have any children (unless
        provided explicitly via the `parent_span` argument), set this to False.
    :type active: bool

    :return: A span.
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

    continue_trace() doesn't start any spans on its own.
    """
    # This is set both on the isolation and the current scope for compatibility
    # reasons. Conceptually, it belongs on the isolation scope, and it also
    # used to be set there in non-span-first mode. But in span first mode, we
    # start spans on the current scope, regardless of type, like JS does, so we
    # need to set the propagation context there.
    sentry_sdk.get_isolation_scope().generate_propagation_context(
        incoming,
    )
    return sentry_sdk.get_current_scope().generate_propagation_context(
        incoming,
    )


def new_trace() -> None:
    """
    Resets the propagation context, forcing a new trace.

    This function sets the propagation context on the scope. Any span started
    in the updated scope will start its own trace.

    new_trace() doesn't start any spans on its own.
    """
    sentry_sdk.get_isolation_scope().set_new_propagation_context()
    sentry_sdk.get_current_scope().set_new_propagation_context()


class StreamedSpan:
    """
    A span holds timing information of a block of code.

    Spans can have multiple child spans thus forming a span tree.

    This is the Span First span implementation. The original transaction-based
    span implementation lives in tracing.Span.
    """

    __slots__ = (
        "_name",
        "_attributes",
        "_active",
        "_span_id",
        "_trace_id",
        "parent_span_id",
        "segment",
        "_sampled",
        "parent_sampled",
        "start_timestamp",
        "timestamp",
        "_status",
        "_start_timestamp_monotonic_ns",
        "_scope",
        "_context_manager_state",
        "_continuous_profile",
        "_baggage",
        "_finished",
        "_last_valid_parent_id",
        "_sample_rand",
        "_sample_rate",
    )

    def __init__(
        self,
        *,
        name: str,
        scope: "sentry_sdk.Scope",
        attributes: "Optional[Attributes]" = None,
        active: bool = True,
        # TODO[span-first]: would be good to actually take this propagation
        # context stuff directly from the PropagationContext, but for that
        # we'd actually need to refactor PropagationContext to stay in sync
        # with what's going on (e.g. update the current span_id) and not just
        # update when a trace is continued
        trace_id: "Optional[str]" = None,
        parent_span_id: "Optional[str]" = None,
        parent_sampled: "Optional[bool]" = None,
        baggage: "Optional[Baggage]" = None,
        segment: "Optional[StreamedSpan]" = None,
        sampled: "Optional[bool]" = None,
        sample_rate: "Optional[float]" = None,
        sample_rand: "Optional[float]" = None,
        last_valid_parent_id: "Optional[str]" = None,
    ) -> None:
        self._scope = scope
        self._active = active

        self._name: str = name
        self._attributes: "Attributes" = {}
        if attributes:
            for attribute, value in attributes.items():
                self.set_attribute(attribute, value)

        self._trace_id: "Optional[str]" = trace_id
        self.parent_span_id = parent_span_id
        self.parent_sampled = parent_sampled
        self.segment = segment or self

        self.start_timestamp = datetime.now(timezone.utc)

        try:
            # profiling depends on this value and requires that
            # it is measured in nanoseconds
            self._start_timestamp_monotonic_ns = nanosecond_time()
        except AttributeError:
            pass

        self.timestamp: "Optional[datetime]" = None
        self._finished: bool = False
        self._span_id: "Optional[str]" = None

        self.set_status(SpanStatus.OK)
        self.set_attribute("sentry.span.source", SegmentSource.CUSTOM.value)

        self._last_valid_parent_id: "Optional[str]" = last_valid_parent_id

        self._baggage = baggage
        self._sample_rand = sample_rand
        self._sample_rate = sample_rate

        self._continuous_profile: "Optional[ContinuousProfile]" = None

        self._update_active_thread()
        self._set_profile_id(get_profiler_id())

        self._start()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"name={self._name}, "
            f"trace_id={self.trace_id}, "
            f"span_id={self.span_id}, "
            f"parent_span_id={self.parent_span_id}, "
            f"sampled={self.sampled}, "
            f"active={self._active})>"
        )

    def __enter__(self) -> "StreamedSpan":
        return self

    def __exit__(
        self, ty: "Optional[Any]", value: "Optional[Any]", tb: "Optional[Any]"
    ) -> None:
        if value is not None and should_be_treated_as_error(ty, value):
            self.set_status(SpanStatus.ERROR)

        self._end()

    def _start(self) -> None:
        scope = self._scope or sentry_sdk.get_current_scope()
        if self._active:
            old_span = scope.span
            scope.span = self
            self._context_manager_state = (scope, old_span)

        if self.is_segment():
            scope._update_sample_rate_from_segment(self)
            scope._start_profile_on_segment(self)

    def _end(self) -> None:
        if self._finished is True:
            # This span is already finished, ignore.
            return

        # Stop the profiler
        if self.is_segment():
            if self._continuous_profile is not None:
                self._continuous_profile.stop()

        # Detach from scope
        if self._active:
            with capture_internal_exceptions():
                scope, old_span = self._context_manager_state
                del self._context_manager_state
                scope.span = old_span
        else:
            scope = self._scope or sentry_sdk.get_current_scope()

        client = sentry_sdk.get_client()
        if not client.is_active():
            return

        self._set_segment_attributes()

        if self.timestamp is None:
            try:
                elapsed = nanosecond_time() - self._start_timestamp_monotonic_ns
                self.timestamp = self.start_timestamp + timedelta(
                    microseconds=elapsed / 1000
                )
            except AttributeError:
                self.timestamp = datetime.now(timezone.utc)

        self._finished = True
        scope._capture_span(self)

    def end(self, end_timestamp: "Optional[Union[float, datetime]]" = None) -> None:
        """
        Finish this span and queue it for sending.

        :param end_timestamp: End timestamp to use instead of current time.
        :type end_timestamp: "Optional[Union[float, datetime]]"
        """
        try:
            if end_timestamp:
                if isinstance(end_timestamp, float):
                    end_timestamp = datetime.fromtimestamp(end_timestamp, timezone.utc)
                self.timestamp = end_timestamp
        except AttributeError:
            pass

        self._end()

    def finish(self, end_timestamp: "Optional[Union[float, datetime]]" = None) -> None:
        warnings.warn(
            "span.finish() is deprecated. Use span.end() instead.",
            stacklevel=2,
            category=DeprecationWarning,
        )

        self.end(end_timestamp)

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

    def get_status(self) -> "Union[SpanStatus, str]":
        if self._status in {s.value for s in SpanStatus}:
            return SpanStatus(self._status)

        return self._status

    def set_status(self, status: "Union[SpanStatus, str]") -> None:
        if isinstance(status, Enum):
            status = status.value

        self._status = status

    def set_http_status(self, http_status: int) -> None:
        self.set_attribute(SPANDATA.HTTP_STATUS_CODE, http_status)

        if http_status >= 400:
            self.set_status(SpanStatus.ERROR)
        else:
            self.set_status(SpanStatus.OK)

    def get_name(self) -> str:
        return self._name

    def set_name(self, name: str) -> None:
        self._name = name

    def is_segment(self) -> bool:
        return self.segment == self

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

    def dynamic_sampling_context(self) -> "dict[str, str]":
        return self.segment.get_baggage().dynamic_sampling_context()

    def to_traceparent(self) -> str:
        if self.sampled is True:
            sampled = "1"
        elif self.sampled is False:
            sampled = "0"
        else:
            sampled = None

        traceparent = "%s-%s" % (self.trace_id, self.span_id)
        if sampled is not None:
            traceparent += "-%s" % (sampled,)

        return traceparent

    def to_baggage(self) -> "Optional[Baggage]":
        if self.segment:
            return self.segment.get_baggage()
        return None

    def iter_headers(self) -> "Iterator[tuple[str, str]]":
        if not self.segment:
            return

        yield SENTRY_TRACE_HEADER_NAME, self.to_traceparent()

        baggage = self.segment.get_baggage().serialize()
        if baggage:
            yield BAGGAGE_HEADER_NAME, baggage

    def _update_active_thread(self) -> None:
        thread_id, thread_name = get_current_thread_meta()
        self._set_thread(thread_id, thread_name)

    def _set_thread(
        self, thread_id: "Optional[int]", thread_name: "Optional[str]"
    ) -> None:
        if thread_id is not None:
            self.set_attribute(SPANDATA.THREAD_ID, str(thread_id))

            if thread_name is not None:
                self.set_attribute(SPANDATA.THREAD_NAME, thread_name)

    def _set_profile_id(self, profiler_id: "Optional[str]") -> None:
        if profiler_id is not None:
            self.set_attribute("sentry.profiler_id", profiler_id)

    def get_baggage(self) -> "Baggage":
        """
        Return the :py:class:`~sentry_sdk.tracing_utils.Baggage` associated with
        the segment.

        The first time a new baggage with Sentry items is made, it will be frozen.
        """
        if not self._baggage or self._baggage.mutable:
            self._baggage = Baggage.populate_from_segment(self)

        return self._baggage

    # TODO: Populate other fields as necessary
    def get_trace_context(self) -> "dict[str, Any]":
        warnings.warn(
            "StreamedSpan.get_trace_context is probably incorrect in streaming mode.",
            DeprecationWarning,
            stacklevel=2,
        )

        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
        }

    def _set_segment_attributes(self) -> None:
        if not self.is_segment():
            self.set_attribute("sentry.segment.id", self.segment.span_id)

        self.set_attribute("sentry.segment.name", self.segment._name)


class NoOpStreamedSpan(StreamedSpan):
    __slots__ = (
        "_name",
        "_span_id",
        "_trace_id",
        "segment",
        "_scope",
        "_context_manager_state",
        "_unsampled_reason",
    )

    def __init__(
        self,
        trace_id: str,
        unsampled_reason: "Optional[str]" = None,
        scope: "Optional[sentry_sdk.Scope]" = None,
        **kwargs: "Any",
    ) -> None:
        self.segment = None  # type: ignore[assignment]
        self._trace_id = trace_id
        self._scope = scope  # type: ignore[assignment]
        self._unsampled_reason = unsampled_reason

        self._start()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(sampled={self.sampled})>"

    def __enter__(self) -> "NoOpStreamedSpan":
        return self

    def __exit__(
        self, ty: "Optional[Any]", value: "Optional[Any]", tb: "Optional[Any]"
    ) -> None:
        self._end()

    def _start(self) -> None:
        if self._scope is None:
            return self

        scope = self._scope or sentry_sdk.get_current_scope()
        old_span = scope.span
        scope.span = self
        self._context_manager_state = (scope, old_span)

    def _end(self) -> None:
        client = sentry_sdk.get_client()
        if not client.is_active():
            return
        transport = client.transport
        if not transport:
            return

        logger.debug("Discarding span because sampled = False")
        transport.record_lost_event(
            reason=self._unsampled_reason or "sample_rate",
            data_category="span",
            quantity=1,
        )

        if self._scope is None:
            return

        with capture_internal_exceptions():
            scope, old_span = self._context_manager_state
            del self._context_manager_state
            scope.span = old_span

    def end(self, end_timestamp: "Optional[Union[float, datetime]]" = None) -> None:
        self._end()

    def finish(self, end_timestamp: "Optional[Union[float, datetime]]" = None) -> None:
        self._end()

    def get_attributes(self) -> "Attributes":
        return {}

    def set_attribute(self, key: str, value: "AttributeValue") -> None:
        pass

    def set_attributes(self, attributes: "Attributes") -> None:
        pass

    def remove_attribute(self, key: str) -> None:
        pass

    def set_name(self, name: str) -> None:
        pass

    def get_name(self) -> str:
        return ""

    def is_segment(self) -> bool:
        return False

    def to_traceparent(self) -> str:
        propagation_context = (
            sentry_sdk.get_current_scope().get_active_propagation_context()
        )

        return f"{propagation_context.trace_id}-{propagation_context.span_id}-0"

    @property
    def sampled(self) -> "Optional[bool]":
        return False

    def _set_segment_attributes(self) -> None:
        pass


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
