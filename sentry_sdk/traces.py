"""
The API in this file is only meant to be used in span streaming mode.

You can enable span streaming mode via
sentry_sdk.init(_experiments={"trace_lifecycle": "stream"}).
"""

import uuid
import warnings
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Pattern

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.profiler.continuous_profiler import get_profiler_id
from sentry_sdk.tracing_utils import (
    Baggage,
    _generate_sample_rand,
    has_span_streaming_enabled,
    has_tracing_enabled,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    format_attribute,
    get_current_thread_meta,
    is_valid_sample_rate,
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


FLAGS_CAPACITY = 10

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
- moved _flags_capacity to a const
"""


def start_span(
    name: str,
    attributes: "Optional[Attributes]" = None,
    parent_span: "Optional[StreamedSpan]" = None,
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
    :return: A span.
    :rtype: StreamedSpan
    """
    return sentry_sdk.get_current_scope().start_streamed_span(
        name, attributes, parent_span
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
    sentry_sdk.get_current_scope().set_new_propagation_context()


class StreamedSpan:
    """
    A span holds timing information of a block of code.

    Spans can have multiple child spans thus forming a span tree.

    This is the Span First span implementation. The original transaction-based
    span implementation lives in tracing.Span.
    """

    __slots__ = (
        "name",
        "attributes",
        "_span_id",
        "_trace_id",
        "parent_span_id",
        "segment",
        "_sampled",
        "parent_sampled",
        "start_timestamp",
        "timestamp",
        "status",
        "_start_timestamp_monotonic_ns",
        "_scope",
        "_flags",
        "_context_manager_state",
        "_continuous_profile",
        "_baggage",
        "sample_rate",
        "_sample_rand",
        "_finished",
    )

    def __init__(
        self,
        *,
        name: str,
        scope: "sentry_sdk.Scope",
        attributes: "Optional[Attributes]" = None,
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
    ) -> None:
        self._scope = scope

        self.name: str = name
        self.attributes: "Attributes" = attributes or {}

        self._trace_id = trace_id
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

        self.status: str = SpanStatus.OK.value
        self.set_source(SegmentSource.CUSTOM)
        # XXX[span-first] ^ populate this correctly

        self._sampled: "Optional[bool]" = None
        self.sample_rate: "Optional[float]" = None

        # XXX[span-first]: just do this for segments?
        self._baggage = baggage
        baggage_sample_rand = (
            None if self._baggage is None else self._baggage._sample_rand()
        )
        if baggage_sample_rand is not None:
            self._sample_rand = baggage_sample_rand
        else:
            self._sample_rand = _generate_sample_rand(self.trace_id)

        self._flags: dict[str, bool] = {}
        self._continuous_profile: "Optional[ContinuousProfile]" = None

        self._update_active_thread()
        self._set_profile_id(get_profiler_id())

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"name={self.name}, "
            f"trace_id={self.trace_id}, "
            f"span_id={self.span_id}, "
            f"parent_span_id={self.parent_span_id}, "
            f"sampled={self.sampled})>"
        )

    def __enter__(self) -> "StreamedSpan":
        scope = self._scope or sentry_sdk.get_current_scope()
        old_span = scope.span
        scope.span = self
        self._context_manager_state = (scope, old_span)

        if self.is_segment():
            sampling_context = {
                "name": self.name,
                "trace_id": self.trace_id,
                "span_id": self.span_id,
                "parent_span_id": self.parent_span_id,
                "parent_sampled": self.parent_sampled,
                "attributes": self.attributes,
            }
            custom_sampling_context = (
                scope.get_active_propagation_context()._custom_sampling_context
            )
            if custom_sampling_context:
                sampling_context.update(custom_sampling_context)

            # Use traces_sample_rate, traces_sampler, and/or inheritance to make a
            # sampling decision
            self._set_sampling_decision(sampling_context=sampling_context)

            scope._update_sample_rate_from_segment(self)
            scope._start_profile_on_segment(self)

        return self

    def __exit__(
        self, ty: "Optional[Any]", value: "Optional[Any]", tb: "Optional[Any]"
    ) -> None:
        if self.is_segment():
            if self._continuous_profile is not None:
                self._continuous_profile.stop()

        if value is not None and should_be_treated_as_error(ty, value):
            self.set_status(SpanStatus.ERROR)

        with capture_internal_exceptions():
            scope, old_span = self._context_manager_state
            del self._context_manager_state
            self._end(scope=scope)
            scope.span = old_span

    def start(self) -> "StreamedSpan":
        """
        Start this span.

        Only usable if the span was not started via the `with start_span():`
        context manager, since that starts it automatically.
        """
        return self.__enter__()

    def finish(self, end_timestamp: "Optional[Union[float, datetime]]" = None) -> None:
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

        self.__exit__(None, None, None)

    def _end(
        self,
        scope: "Optional[sentry_sdk.Scope]" = None,
    ) -> None:
        client = sentry_sdk.get_client()
        if not client.is_active():
            return

        self._set_segment_attributes()

        scope: "Optional[sentry_sdk.Scope]" = (
            scope or self._scope or sentry_sdk.get_current_scope()
        )

        # Explicit check against False needed because self.sampled might be None
        if self.sampled is False:
            logger.debug("Discarding span because sampled = False")

            # This is not entirely accurate because discards here are not
            # exclusively based on sample rate but also traces sampler, but
            # we handle this the same here.
            if client.transport and has_tracing_enabled(client.options):
                if client.monitor and client.monitor.downsample_factor > 0:
                    reason = "backpressure"
                else:
                    reason = "sample_rate"

                client.transport.record_lost_event(reason, data_category="span")

            return

        if self.sampled is None:
            logger.warning("Discarding transaction without sampling decision.")

        if self._finished is True:
            # This span is already finished, ignore.
            return

        if self.timestamp is None:
            try:
                elapsed = nanosecond_time() - self._start_timestamp_monotonic_ns
                self.timestamp = self.start_timestamp + timedelta(
                    microseconds=elapsed / 1000
                )
            except AttributeError:
                self.timestamp = datetime.now(timezone.utc)

        if self.segment.sampled:  # XXX this should just use its own sampled
            sentry_sdk.get_current_scope()._capture_span(self)

        self._finished = True

    def get_attributes(self) -> "Attributes":
        return self.attributes

    def set_attribute(self, key: str, value: "AttributeValue") -> None:
        self.attributes[key] = format_attribute(value)

    def remove_attribute(self, key: str) -> None:
        try:
            del self.attributes[key]
        except KeyError:
            pass

    def set_attributes(self, attributes: "Attributes") -> None:
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def set_status(self, status: "Union[SpanStatus, str]") -> None:
        if isinstance(status, Enum):
            status = status.value

        self.status = status

    def get_name(self) -> str:
        return self.name

    def set_name(self, name: str) -> None:
        self.name = name

    def set_flag(self, flag: str, result: bool) -> None:
        if len(self._flags) < FLAGS_CAPACITY:
            self._flags[flag] = result

    def set_op(self, op: str) -> None:
        self.set_attribute("sentry.op", op)

    def set_origin(self, origin: str) -> None:
        self.set_attribute("sentry.origin", origin)

    def set_source(self, source: "Union[str, SegmentSource]") -> None:
        if isinstance(source, Enum):
            source = source.value

        self.set_attribute("sentry.span.source", source)

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
        if self._sampled is not None:
            return self._sampled

        if not self.is_segment():
            self._sampled = self.parent_sampled

        return self._sampled

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

    def set_http_status(self, http_status: int) -> None:
        self.set_attribute(SPANDATA.HTTP_STATUS_CODE, http_status)

        if http_status >= 400:
            self.set_status(SpanStatus.ERROR)
        else:
            self.set_status(SpanStatus.OK)

    def get_baggage(self) -> "Baggage":
        """
        Return the :py:class:`~sentry_sdk.tracing_utils.Baggage` associated with
        the segment.

        The first time a new baggage with Sentry items is made, it will be frozen.
        """
        if not self._baggage or self._baggage.mutable:
            self._baggage = Baggage.populate_from_segment(self)

        return self._baggage

    def _set_sampling_decision(self, sampling_context: "SamplingContext") -> None:
        """Set a segment's sampling decision."""
        client = sentry_sdk.get_client()

        # nothing to do if tracing is disabled
        if not has_tracing_enabled(client.options):
            self._sampled = False
            return

        if not self.is_segment():
            return

        traces_sampler_defined = callable(client.options.get("traces_sampler"))

        # We would have bailed already if neither `traces_sampler` nor
        # `traces_sample_rate` were defined, so one of these should work; prefer
        # the hook if so
        if traces_sampler_defined:
            sample_rate = client.options["traces_sampler"](sampling_context)
        else:
            if sampling_context["parent_sampled"] is not None:
                sample_rate = sampling_context["parent_sampled"]
            else:
                sample_rate = client.options["traces_sample_rate"]

        # Since this is coming from the user (or from a function provided by the
        # user), who knows what we might get. (The only valid values are
        # booleans or numbers between 0 and 1.)
        if not is_valid_sample_rate(sample_rate, source="Tracing"):
            logger.warning(
                f"[Tracing] Discarding {self.name} because of invalid sample rate."
            )
            self._sampled = False
            return

        self.sample_rate = float(sample_rate)

        if client.monitor:
            self.sample_rate /= 2**client.monitor.downsample_factor

        # if the function returned 0 (or false), or if `traces_sample_rate` is
        # 0, it's a sign the transaction should be dropped
        if not self.sample_rate:
            if traces_sampler_defined:
                reason = "traces_sampler returned 0 or False"
            else:
                reason = "traces_sample_rate is set to 0"

            logger.debug(f"[Tracing] Discarding {self.name} because {reason}")
            self._sampled = False
            return

        # Now we roll the dice.
        self._sampled = self._sample_rand < self.sample_rate

        if self.sampled:
            logger.debug(f"[Tracing] Starting {self.name}")
        else:
            logger.debug(
                f"[Tracing] Discarding {self.name} because it's not included in the random sample (sampling rate = {self.sample_rate})"
            )

    def _set_segment_attributes(self) -> None:
        if not self.is_segment():
            self.set_attribute("sentry.segment.id", self.segment.span_id)

        self.set_attribute("sentry.segment.name", self.segment.name)


class NoOpStreamedSpan(StreamedSpan):
    # XXX[span-first]: make this actually no-op
    def __init__(
        self,
        *args: "Any",
        last_valid_parent: "Optional[StreamedSpan]" = None,
        **kwargs: "Any",
    ):
        self._sampled = False
        self.last_valid_parent = last_valid_parent

    def __enter__(self) -> "NoOpStreamedSpan":
        return self

    def __exit__(
        self, ty: "Optional[Any]", value: "Optional[Any]", tb: "Optional[Any]"
    ) -> None:
        return


def trace(
    func: "Optional[Callable[P, R]]" = None,
    *,
    name: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
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
    )

    if func:
        return decorator(func)
    else:
        return decorator
