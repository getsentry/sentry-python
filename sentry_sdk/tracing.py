from datetime import datetime
from enum import Enum
import json
import warnings

from opentelemetry import trace as otel_trace, context
from opentelemetry.trace import (
    format_trace_id,
    format_span_id,
    Span as OtelSpan,
    TraceState,
    get_current_span,
    INVALID_SPAN,
)
from opentelemetry.trace.status import StatusCode
from opentelemetry.sdk.trace import ReadableSpan

import sentry_sdk
from sentry_sdk.consts import SPANSTATUS, SPANDATA
from sentry_sdk.utils import (
    _serialize_span_attribute,
    get_current_thread_meta,
    should_be_treated_as_error,
)

from typing import TYPE_CHECKING, cast


if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any
    from typing import Dict
    from typing import Iterator
    from typing import Optional
    from typing import overload
    from typing import ParamSpec
    from typing import Tuple
    from typing import Union
    from typing import TypeVar

    from typing_extensions import TypedDict

    P = ParamSpec("P")
    R = TypeVar("R")

    from sentry_sdk._types import (
        MeasurementUnit,
        SamplingContext,
    )

    from sentry_sdk.tracing_utils import Baggage

    class SpanKwargs(TypedDict, total=False):
        op: str
        """
        The span's operation. A list of recommended values is available here:
        https://develop.sentry.dev/sdk/performance/span-operations/
        """

        status: str
        """The span's status. Possible values are listed at https://develop.sentry.dev/sdk/event-payloads/span/"""

        containing_transaction: Optional["Span"]
        """The transaction that this span belongs to."""

        start_timestamp: Optional[Union[datetime, float]]
        """
        The timestamp when the span started. If omitted, the current time
        will be used.
        """

        scope: "sentry_sdk.Scope"
        """The scope to use for this span. If not provided, we use the current scope."""

        origin: Optional[str]
        """
        The origin of the span.
        See https://develop.sentry.dev/sdk/performance/trace-origin/
        Default "manual".
        """

        name: str
        """A string describing what operation is being performed within the span/transaction."""

    class TransactionKwargs(SpanKwargs, total=False):
        source: str
        """
        A string describing the source of the transaction name. This will be used to determine the transaction's type.
        See https://develop.sentry.dev/sdk/event-payloads/transaction/#transaction-annotations for more information.
        Default "custom".
        """

    ProfileContext = TypedDict(
        "ProfileContext",
        {
            "profiler_id": str,
        },
    )

BAGGAGE_HEADER_NAME = "baggage"
SENTRY_TRACE_HEADER_NAME = "sentry-trace"


# Transaction source
# see https://develop.sentry.dev/sdk/event-payloads/transaction/#transaction-annotations
class TransactionSource(str, Enum):
    COMPONENT = "component"
    CUSTOM = "custom"
    ROUTE = "route"
    TASK = "task"
    URL = "url"
    VIEW = "view"

    def __str__(self):
        # type: () -> str
        return self.value


# These are typically high cardinality and the server hates them
LOW_QUALITY_TRANSACTION_SOURCES = [
    TransactionSource.URL,
]

SOURCE_FOR_STYLE = {
    "endpoint": TransactionSource.COMPONENT,
    "function_name": TransactionSource.COMPONENT,
    "handler_name": TransactionSource.COMPONENT,
    "method_and_path_pattern": TransactionSource.ROUTE,
    "path": TransactionSource.URL,
    "route_name": TransactionSource.COMPONENT,
    "route_pattern": TransactionSource.ROUTE,
    "uri_template": TransactionSource.ROUTE,
    "url": TransactionSource.ROUTE,
}

DEFAULT_SPAN_ORIGIN = "manual"
DEFAULT_SPAN_NAME = "<unlabeled span>"

tracer = otel_trace.get_tracer(__name__)


def get_span_status_from_http_code(http_status_code):
    # type: (int) -> str
    """
    Returns the Sentry status corresponding to the given HTTP status code.

    See: https://develop.sentry.dev/sdk/event-payloads/contexts/#trace-context
    """
    if http_status_code < 400:
        return SPANSTATUS.OK

    elif 400 <= http_status_code < 500:
        if http_status_code == 403:
            return SPANSTATUS.PERMISSION_DENIED
        elif http_status_code == 404:
            return SPANSTATUS.NOT_FOUND
        elif http_status_code == 429:
            return SPANSTATUS.RESOURCE_EXHAUSTED
        elif http_status_code == 413:
            return SPANSTATUS.FAILED_PRECONDITION
        elif http_status_code == 401:
            return SPANSTATUS.UNAUTHENTICATED
        elif http_status_code == 409:
            return SPANSTATUS.ALREADY_EXISTS
        else:
            return SPANSTATUS.INVALID_ARGUMENT

    elif 500 <= http_status_code < 600:
        if http_status_code == 504:
            return SPANSTATUS.DEADLINE_EXCEEDED
        elif http_status_code == 501:
            return SPANSTATUS.UNIMPLEMENTED
        elif http_status_code == 503:
            return SPANSTATUS.UNAVAILABLE
        else:
            return SPANSTATUS.INTERNAL_ERROR

    return SPANSTATUS.UNKNOWN_ERROR


class NoOpSpan:
    def __init__(self, **kwargs):
        # type: (Any) -> None
        pass

    def __repr__(self):
        # type: () -> str
        return "<%s>" % self.__class__.__name__

    @property
    def containing_transaction(self):
        # type: () -> Optional[Span]
        return None

    def start_child(self, **kwargs):
        # type: (**Any) -> NoOpSpan
        return NoOpSpan()

    def to_traceparent(self):
        # type: () -> str
        return ""

    def to_baggage(self):
        # type: () -> Optional[Baggage]
        return None

    def get_baggage(self):
        # type: () -> Optional[Baggage]
        return None

    def iter_headers(self):
        # type: () -> Iterator[Tuple[str, str]]
        return iter(())

    def set_tag(self, key, value):
        # type: (str, Any) -> None
        pass

    def set_data(self, key, value):
        # type: (str, Any) -> None
        pass

    def set_status(self, value):
        # type: (str) -> None
        pass

    def set_http_status(self, http_status):
        # type: (int) -> None
        pass

    def is_success(self):
        # type: () -> bool
        return True

    def to_json(self):
        # type: () -> Dict[str, Any]
        return {}

    def get_trace_context(self):
        # type: () -> Any
        return {}

    def get_profile_context(self):
        # type: () -> Any
        return {}

    def finish(
        self,
        end_timestamp=None,  # type: Optional[Union[float, datetime]]
    ):
        # type: (...) -> None
        pass

    def set_measurement(self, name, value, unit=""):
        # type: (str, float, MeasurementUnit) -> None
        pass

    def set_context(self, key, value):
        # type: (str, dict[str, Any]) -> None
        pass

    def init_span_recorder(self, maxlen):
        # type: (int) -> None
        pass

    def _set_initial_sampling_decision(self, sampling_context):
        # type: (SamplingContext) -> None
        pass


class Span:
    """
    OTel span wrapper providing compatibility with the old span interface.
    """

    def __init__(
        self,
        *,
        op=None,  # type: Optional[str]
        description=None,  # type: Optional[str]
        status=None,  # type: Optional[str]
        start_timestamp=None,  # type: Optional[Union[datetime, float]]
        origin=None,  # type: Optional[str]
        name=None,  # type: Optional[str]
        source=TransactionSource.CUSTOM,  # type: str
        attributes=None,  # type: Optional[dict[str, Any]]
        only_if_parent=False,  # type: bool
        parent_span=None,  # type: Optional[Span]
        otel_span=None,  # type: Optional[OtelSpan]
    ):
        # type: (...) -> None
        """
        If otel_span is passed explicitly, just acts as a proxy.

        If only_if_parent is True, just return an INVALID_SPAN
        and avoid instrumentation if there's no active parent span.
        """
        if otel_span is not None:
            self._otel_span = otel_span
        else:
            skip_span = False
            if only_if_parent and parent_span is None:
                parent_span_context = get_current_span().get_span_context()
                skip_span = (
                    not parent_span_context.is_valid or parent_span_context.is_remote
                )

            if skip_span:
                self._otel_span = INVALID_SPAN
            else:
                from sentry_sdk.integrations.opentelemetry.consts import (
                    SentrySpanAttribute,
                )
                from sentry_sdk.integrations.opentelemetry.utils import (
                    convert_to_otel_timestamp,
                )

                if start_timestamp is not None:
                    # OTel timestamps have nanosecond precision
                    start_timestamp = convert_to_otel_timestamp(start_timestamp)

                span_name = name or description or op or DEFAULT_SPAN_NAME

                # Prepopulate some attrs so that they're accessible in traces_sampler
                attributes = attributes or {}
                if op is not None:
                    attributes[SentrySpanAttribute.OP] = op
                if source is not None:
                    attributes[SentrySpanAttribute.SOURCE] = source
                if description is not None:
                    attributes[SentrySpanAttribute.DESCRIPTION] = description

                parent_context = None
                if parent_span is not None:
                    parent_context = otel_trace.set_span_in_context(
                        parent_span._otel_span
                    )

                self._otel_span = tracer.start_span(
                    span_name,
                    context=parent_context,
                    start_time=start_timestamp,
                    attributes=attributes,
                )

                self.origin = origin or DEFAULT_SPAN_ORIGIN
                self.name = span_name

                if status is not None:
                    self.set_status(status)

    def __eq__(self, other):
        # type: (object) -> bool
        if not isinstance(other, Span):
            return False
        return self._otel_span == other._otel_span

    def __repr__(self):
        # type: () -> str
        return (
            "<%s(op=%r, name:%r, trace_id=%r, span_id=%r, parent_span_id=%r, sampled=%r, origin=%r)>"
            % (
                self.__class__.__name__,
                self.op,
                self.name,
                self.trace_id,
                self.span_id,
                self.parent_span_id,
                self.sampled,
                self.origin,
            )
        )

    def __enter__(self):
        # type: () -> Span
        # XXX use_span? https://github.com/open-telemetry/opentelemetry-python/blob/3836da8543ce9751051e38a110c0468724042e62/opentelemetry-api/src/opentelemetry/trace/__init__.py#L547
        #
        # create a Context object with parent set as current span
        ctx = otel_trace.set_span_in_context(self._otel_span)
        # set as the implicit current context
        self._ctx_token = context.attach(ctx)

        # get the new scope that was forked on context.attach
        self.scope = sentry_sdk.get_current_scope()
        self.scope.span = self

        return self

    def __exit__(self, ty, value, tb):
        # type: (Optional[Any], Optional[Any], Optional[Any]) -> None
        if value is not None and should_be_treated_as_error(ty, value):
            self.set_status(SPANSTATUS.INTERNAL_ERROR)
        else:
            status_unset = (
                hasattr(self._otel_span, "status")
                and self._otel_span.status.status_code == StatusCode.UNSET
            )
            if status_unset:
                self.set_status(SPANSTATUS.OK)

        self.finish()
        context.detach(self._ctx_token)
        del self._ctx_token

    @property
    def description(self):
        # type: () -> Optional[str]
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        return self.get_attribute(SentrySpanAttribute.DESCRIPTION)

    @description.setter
    def description(self, value):
        # type: (Optional[str]) -> None
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        self.set_attribute(SentrySpanAttribute.DESCRIPTION, value)

    @property
    def origin(self):
        # type: () -> Optional[str]
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        return self.get_attribute(SentrySpanAttribute.ORIGIN)

    @origin.setter
    def origin(self, value):
        # type: (Optional[str]) -> None
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        self.set_attribute(SentrySpanAttribute.ORIGIN, value)

    @property
    def containing_transaction(self):
        # type: () -> Optional[Span]
        """
        Get the transaction this span is a child of.

        .. deprecated:: 3.0.0
            This will be removed in the future. Use :func:`root_span` instead.
        """
        warnings.warn(
            "Deprecated: This will be removed in the future. Use root_span instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.root_span

    @property
    def root_span(self):
        # type: () -> Optional[Span]
        from sentry_sdk.integrations.opentelemetry.utils import (
            get_sentry_meta,
        )

        root_otel_span = cast(
            "Optional[OtelSpan]", get_sentry_meta(self._otel_span, "root_span")
        )
        return Span(otel_span=root_otel_span) if root_otel_span else None

    @property
    def is_root_span(self):
        # type: () -> bool
        return self.root_span == self

    @property
    def parent_span_id(self):
        # type: () -> Optional[str]
        if (
            not isinstance(self._otel_span, ReadableSpan)
            or self._otel_span.parent is None
        ):
            return None
        return format_span_id(self._otel_span.parent.span_id)

    @property
    def trace_id(self):
        # type: () -> str
        return format_trace_id(self._otel_span.get_span_context().trace_id)

    @property
    def span_id(self):
        # type: () -> str
        return format_span_id(self._otel_span.get_span_context().span_id)

    @property
    def is_valid(self):
        # type: () -> bool
        return self._otel_span.get_span_context().is_valid and isinstance(
            self._otel_span, ReadableSpan
        )

    @property
    def sampled(self):
        # type: () -> Optional[bool]
        return self._otel_span.get_span_context().trace_flags.sampled

    @property
    def sample_rate(self):
        # type: () -> Optional[float]
        from sentry_sdk.integrations.opentelemetry.consts import (
            TRACESTATE_SAMPLE_RATE_KEY,
        )

        sample_rate = self._otel_span.get_span_context().trace_state.get(
            TRACESTATE_SAMPLE_RATE_KEY
        )
        return float(sample_rate) if sample_rate is not None else None

    @property
    def op(self):
        # type: () -> Optional[str]
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        return self.get_attribute(SentrySpanAttribute.OP)

    @op.setter
    def op(self, value):
        # type: (Optional[str]) -> None
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        self.set_attribute(SentrySpanAttribute.OP, value)

    @property
    def name(self):
        # type: () -> Optional[str]
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        return self.get_attribute(SentrySpanAttribute.NAME)

    @name.setter
    def name(self, value):
        # type: (Optional[str]) -> None
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        self.set_attribute(SentrySpanAttribute.NAME, value)

    @property
    def source(self):
        # type: () -> str
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        return (
            self.get_attribute(SentrySpanAttribute.SOURCE) or TransactionSource.CUSTOM
        )

    @source.setter
    def source(self, value):
        # type: (str) -> None
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        self.set_attribute(SentrySpanAttribute.SOURCE, value)

    @property
    def start_timestamp(self):
        # type: () -> Optional[datetime]
        if not isinstance(self._otel_span, ReadableSpan):
            return None

        start_time = self._otel_span.start_time
        if start_time is None:
            return None

        from sentry_sdk.integrations.opentelemetry.utils import (
            convert_from_otel_timestamp,
        )

        return convert_from_otel_timestamp(start_time)

    @property
    def timestamp(self):
        # type: () -> Optional[datetime]
        if not isinstance(self._otel_span, ReadableSpan):
            return None

        end_time = self._otel_span.end_time
        if end_time is None:
            return None

        from sentry_sdk.integrations.opentelemetry.utils import (
            convert_from_otel_timestamp,
        )

        return convert_from_otel_timestamp(end_time)

    def start_child(self, **kwargs):
        # type: (**Any) -> Span
        return Span(parent_span=self, **kwargs)

    def iter_headers(self):
        # type: () -> Iterator[Tuple[str, str]]
        yield SENTRY_TRACE_HEADER_NAME, self.to_traceparent()

        from sentry_sdk.integrations.opentelemetry.utils import (
            serialize_trace_state,
        )

        yield BAGGAGE_HEADER_NAME, serialize_trace_state(self.trace_state)

    def to_traceparent(self):
        # type: () -> str
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

    @property
    def trace_state(self):
        # type: () -> TraceState
        from sentry_sdk.integrations.opentelemetry.utils import (
            get_trace_state,
        )

        return get_trace_state(self._otel_span)

    def to_baggage(self):
        # type: () -> Baggage
        return self.get_baggage()

    def get_baggage(self):
        # type: () -> Baggage
        from sentry_sdk.integrations.opentelemetry.utils import (
            baggage_from_trace_state,
        )

        return baggage_from_trace_state(self.trace_state)

    def set_tag(self, key, value):
        # type: (str, Any) -> None
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        self.set_attribute(f"{SentrySpanAttribute.TAG}.{key}", value)

    def set_data(self, key, value):
        # type: (str, Any) -> None
        # TODO-neel-potel we cannot add dicts here
        self.set_attribute(key, value)

    def get_attribute(self, name):
        # type: (str) -> Optional[Any]
        if (
            not isinstance(self._otel_span, ReadableSpan)
            or not self._otel_span.attributes
        ):
            return None
        return self._otel_span.attributes.get(name)

    def set_attribute(self, key, value):
        # type: (str, Any) -> None
        # otel doesn't support None as values, preferring to not set the key
        # at all instead
        if value is None:
            return
        serialized_value = _serialize_span_attribute(value)
        if serialized_value is None:
            return

        self._otel_span.set_attribute(key, serialized_value)

    @property
    def status(self):
        # type: () -> Optional[str]
        """
        Return the Sentry `SPANSTATUS` corresponding to the underlying OTel status.
        Because differences in possible values in OTel `StatusCode` and
        Sentry `SPANSTATUS` it can not be guaranteed that the status
        set in `set_status()` will be the same as the one returned here.
        """
        if not isinstance(self._otel_span, ReadableSpan):
            return None

        if self._otel_span.status.status_code == StatusCode.UNSET:
            return None
        elif self._otel_span.status.status_code == StatusCode.OK:
            return SPANSTATUS.OK
        else:
            return SPANSTATUS.UNKNOWN_ERROR

    def set_status(self, status):
        # type: (str) -> None
        if status == SPANSTATUS.OK:
            otel_status = StatusCode.OK
            otel_description = None
        else:
            otel_status = StatusCode.ERROR
            otel_description = status

        self._otel_span.set_status(otel_status, otel_description)

    def set_measurement(self, name, value, unit=""):
        # type: (str, float, MeasurementUnit) -> None
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        # Stringify value here since OTel expects all seq items to be of one type
        self.set_attribute(
            f"{SentrySpanAttribute.MEASUREMENT}.{name}", (str(value), unit)
        )

    def set_thread(self, thread_id, thread_name):
        # type: (Optional[int], Optional[str]) -> None
        if thread_id is not None:
            self.set_data(SPANDATA.THREAD_ID, str(thread_id))

            if thread_name is not None:
                self.set_data(SPANDATA.THREAD_NAME, thread_name)

    def update_active_thread(self):
        # type: () -> None
        thread_id, thread_name = get_current_thread_meta()
        self.set_thread(thread_id, thread_name)

    def set_http_status(self, http_status):
        # type: (int) -> None
        self.set_data(SPANDATA.HTTP_STATUS_CODE, http_status)
        self.set_status(get_span_status_from_http_code(http_status))

    def is_success(self):
        # type: () -> bool
        return self.status == SPANSTATUS.OK

    def finish(self, end_timestamp=None):
        # type: (Optional[Union[float, datetime]]) -> None
        if end_timestamp is not None:
            from sentry_sdk.integrations.opentelemetry.utils import (
                convert_to_otel_timestamp,
            )

            self._otel_span.end(convert_to_otel_timestamp(end_timestamp))
        else:
            self._otel_span.end()

    def to_json(self):
        # type: () -> dict[str, Any]
        """
        Only meant for testing. Not used internally anymore.
        """
        if not isinstance(self._otel_span, ReadableSpan):
            return {}
        return json.loads(self._otel_span.to_json())

    def get_trace_context(self):
        # type: () -> dict[str, Any]
        if not isinstance(self._otel_span, ReadableSpan):
            return {}

        from sentry_sdk.integrations.opentelemetry.utils import (
            get_trace_context,
        )

        return get_trace_context(self._otel_span)

    def set_context(self, key, value):
        # type: (str, Any) -> None
        from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute

        # TODO-neel-potel we cannot add dicts here

        self.set_attribute(f"{SentrySpanAttribute.CONTEXT}.{key}", value)


# TODO-neel-potel add deprecation
Transaction = Span


if TYPE_CHECKING:

    @overload
    def trace(func=None):
        # type: (None) -> Callable[[Callable[P, R]], Callable[P, R]]
        pass

    @overload
    def trace(func):
        # type: (Callable[P, R]) -> Callable[P, R]
        pass


def trace(func=None):
    # type: (Optional[Callable[P, R]]) -> Union[Callable[P, R], Callable[[Callable[P, R]], Callable[P, R]]]
    """
    Decorator to start a child span under the existing current transaction.
    If there is no current transaction, then nothing will be traced.

    .. code-block::
        :caption: Usage

        import sentry_sdk

        @sentry_sdk.trace
        def my_function():
            ...

        @sentry_sdk.trace
        async def my_async_function():
            ...
    """
    from sentry_sdk.tracing_utils import start_child_span_decorator

    # This patterns allows usage of both @sentry_traced and @sentry_traced(...)
    # See https://stackoverflow.com/questions/52126071/decorator-with-arguments-avoid-parenthesis-when-no-arguments/52126278
    if func:
        return start_child_span_decorator(func)
    else:
        return start_child_span_decorator
