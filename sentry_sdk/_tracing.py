import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.profiler.continuous_profiler import get_profiler_id
from sentry_sdk.tracing_utils import (
    Baggage,
    has_span_streaming_enabled,
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
    from typing import Any, Optional, Union
    from sentry_sdk._types import Attributes, AttributeValue
    from sentry_sdk.scope import Scope


FLAGS_CAPACITY = 10

"""
TODO[span-first] / notes
- redis, http, subprocess breadcrumbs (maybe_create_breadcrumbs_from_span) work
  on op, change or ignore?
- @trace
- tags
- initial status: OK? or unset?
- dropped spans are not migrated
- recheck transaction.finish <-> Streamedspan.end
- profile not part of the event, how to send?
- maybe: use getters/setter OR properties but not both
- add size-based flushing to buffer(s)
- migrate transaction sample_rand logic

Notes:
- removed ability to provide a start_timestamp
- moved _flags_capacity to a const
"""


def start_span(
    name: str,
    attributes: "Optional[Attributes]" = None,
    parent_span: "Optional[StreamedSpan]" = None,
) -> "StreamedSpan":
    return sentry_sdk.get_current_scope().start_streamed_span()


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
        "_span_id",
        "_trace_id",
        "_parent_span_id",
        "_segment",
        "_sampled",
        "_start_timestamp",
        "_timestamp",
        "_status",
        "_start_timestamp_monotonic_ns",
        "_scope",
        "_flags",
        "_context_manager_state",
        "_profile",
        "_continuous_profile",
        "_baggage",
        "_sample_rate",
        "_sample_rand",
    )

    def __init__(
        self,
        name: str,
        trace_id: str,
        attributes: "Optional[Attributes]" = None,
        parent_span_id: "Optional[str]" = None,
        segment: "Optional[StreamedSpan]" = None,
        scope: "Optional[Scope]" = None,
    ) -> None:
        self._name: str = name
        self._attributes: "Attributes" = attributes

        self._trace_id = trace_id
        self._parent_span_id = parent_span_id
        self._segment = segment or self

        self._start_timestamp = datetime.now(timezone.utc)

        try:
            # profiling depends on this value and requires that
            # it is measured in nanoseconds
            self._start_timestamp_monotonic_ns = nanosecond_time()
        except AttributeError:
            pass

        self._timestamp: "Optional[datetime]" = None
        self._span_id: "Optional[str]" = None
        self._status: SpanStatus = SpanStatus.OK
        self._sampled: "Optional[bool]" = None
        self._scope: "Optional[Scope]" = scope  # TODO[span-first] when are we starting a span with a specific scope? is this needed?
        self._flags: dict[str, bool] = {}

        self._update_active_thread()
        self._set_profiler_id(get_profiler_id())

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"name={self._name}, "
            f"trace_id={self._trace_id}, "
            f"span_id={self._span_id}, "
            f"parent_span_id={self._parent_span_id}, "
            f"sampled={self._sampled})>"
        )

    def __enter__(self) -> "StreamedSpan":
        scope = self._scope or sentry_sdk.get_current_scope()
        old_span = scope.span
        scope.span = self
        self._context_manager_state = (scope, old_span)

        if self.is_segment() and self._profile is not None:
            self._profile.__enter__()

        return self

    def __exit__(
        self, ty: "Optional[Any]", value: "Optional[Any]", tb: "Optional[Any]"
    ) -> None:
        if self.is_segment():
            if self._profile is not None:
                self._profile.__exit__(ty, value, tb)

            if self._continuous_profile is not None:
                self._continuous_profile.stop()

        if value is not None and should_be_treated_as_error(ty, value):
            self.set_status(SpanStatus.ERROR)

        with capture_internal_exceptions():
            scope, old_span = self._context_manager_state
            del self._context_manager_state
            self.end(scope=scope)
            scope.span = old_span

    def end(
        self,
        end_timestamp: "Optional[Union[float, datetime]]" = None,
        scope: "Optional[sentry_sdk.Scope]" = None,
    ) -> "Optional[str]":
        """
        Set the end timestamp of the span.

        :param end_timestamp: Optional timestamp that should
            be used as timestamp instead of the current time.
        :param scope: The scope to use for this transaction.
            If not provided, the current scope will be used.
        """
        client = sentry_sdk.get_client()
        if not client.is_active():
            return None

        scope: "Optional[sentry_sdk.Scope]" = (
            scope or self._scope or sentry_sdk.get_current_scope()
        )

        # Explicit check against False needed because self.sampled might be None
        if self._sampled is False:
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

            return None

        if self._sampled is None:
            logger.warning("Discarding transaction without sampling decision.")

        if self.timestamp is not None:
            # This span is already finished, ignore.
            return None

        try:
            if end_timestamp:
                if isinstance(end_timestamp, float):
                    end_timestamp = datetime.fromtimestamp(end_timestamp, timezone.utc)
                self.timestamp = end_timestamp
            else:
                elapsed = nanosecond_time() - self._start_timestamp_monotonic_ns
                self.timestamp = self._start_timestamp + timedelta(
                    microseconds=elapsed / 1000
                )
        except AttributeError:
            self.timestamp = datetime.now(timezone.utc)

        if self.segment.sampled:
            client._capture_span(self)
        return

    def get_attributes(self) -> "Attributes":
        return self._attributes

    def set_attribute(self, key: str, value: "AttributeValue") -> None:
        self._attributes[key] = format_attribute(value)

    def set_attributes(self, attributes: "Attributes") -> None:
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def set_status(self, status: SpanStatus) -> None:
        self._status = status

    def get_name(self) -> str:
        return self._name

    def set_name(self, name: str) -> None:
        self._name = name

    @property
    def segment(self) -> "StreamedSpan":
        return self._segment

    def is_segment(self) -> bool:
        return self.segment == self

    @property
    def sampled(self) -> "Optional[bool]":
        return self._sampled

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
    def dynamic_sampling_context(self) -> str:
        # TODO
        return self.segment.get_baggage().dynamic_sampling_context()

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

    def _set_profiler_id(self, profiler_id: "Optional[str]") -> None:
        if profiler_id is not None:
            self.set_attribute(SPANDATA.PROFILER_ID, profiler_id)

    def _set_http_status(self, http_status: int) -> None:
        self.set_attribute(SPANDATA.HTTP_STATUS_CODE, http_status)

        if http_status >= 400:
            self.set_status(SpanStatus.ERROR)
        else:
            self.set_status(SpanStatus.OK)

    def _get_baggage(self) -> "Baggage":
        """
        Return the :py:class:`~sentry_sdk.tracing_utils.Baggage` associated with
        the segment.

        The first time a new baggage with Sentry items is made, it will be frozen.
        """
        if not self._baggage or self._baggage.mutable:
            self._baggage = Baggage.populate_from_segment(self)

        return self._baggage
