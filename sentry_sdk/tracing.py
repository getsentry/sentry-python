from __future__ import annotations
from datetime import datetime
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
from opentelemetry.trace.status import Status, StatusCode
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.version import __version__ as otel_version

from sentry_sdk.consts import (
    DEFAULT_SPAN_NAME,
    DEFAULT_SPAN_ORIGIN,
    BAGGAGE_HEADER_NAME,
    SENTRY_TRACE_HEADER_NAME,
    SPANSTATUS,
    SPANDATA,
    TransactionSource,
)
from sentry_sdk.opentelemetry.consts import (
    TRACESTATE_SAMPLE_RATE_KEY,
    SentrySpanAttribute,
)
from sentry_sdk.opentelemetry.utils import (
    baggage_from_trace_state,
    convert_from_otel_timestamp,
    convert_to_otel_timestamp,
    get_trace_context,
    get_trace_state,
    get_sentry_meta,
    serialize_trace_state,
)
from sentry_sdk.tracing_utils import (
    get_span_status_from_http_code,
    _is_span_origin_excluded,
)
from sentry_sdk.utils import (
    _serialize_span_attribute,
    get_current_thread_meta,
    parse_version,
    should_be_treated_as_error,
)

from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from typing import (
        Callable,
        Any,
        Dict,
        Iterator,
        Optional,
        ParamSpec,
        Tuple,
        Union,
        TypeVar,
    )
    from sentry_sdk._types import SamplingContext
    from sentry_sdk.tracing_utils import Baggage

    P = ParamSpec("P")
    R = TypeVar("R")


_FLAGS_CAPACITY = 10
_OTEL_VERSION = parse_version(otel_version)

tracer = otel_trace.get_tracer(__name__)


class NoOpSpan:
    def __init__(self, **kwargs: Any) -> None:
        pass

    def __repr__(self) -> str:
        return "<%s>" % self.__class__.__name__

    @property
    def root_span(self) -> Optional[Span]:
        return None

    def start_child(self, **kwargs: Any) -> NoOpSpan:
        return NoOpSpan()

    def to_traceparent(self) -> str:
        return ""

    def to_baggage(self) -> Optional[Baggage]:
        return None

    def get_baggage(self) -> Optional[Baggage]:
        return None

    def iter_headers(self) -> Iterator[Tuple[str, str]]:
        return iter(())

    def set_tag(self, key: str, value: Any) -> None:
        pass

    def set_data(self, key: str, value: Any) -> None:
        pass

    def set_status(self, value: str) -> None:
        pass

    def set_http_status(self, http_status: int) -> None:
        pass

    def is_success(self) -> bool:
        return True

    def to_json(self) -> Dict[str, Any]:
        return {}

    def get_trace_context(self) -> Any:
        return {}

    def get_profile_context(self) -> Any:
        return {}

    def finish(self, end_timestamp: Optional[Union[float, datetime]] = None) -> None:
        pass

    def set_context(self, key: str, value: dict[str, Any]) -> None:
        pass

    def init_span_recorder(self, maxlen: int) -> None:
        pass

    def _set_initial_sampling_decision(self, sampling_context: SamplingContext) -> None:
        pass


class Span:
    """
    OTel span wrapper providing compatibility with the old span interface.
    """

    def __init__(
        self,
        *,
        op: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        sampled: Optional[bool] = None,
        start_timestamp: Optional[Union[datetime, float]] = None,
        origin: Optional[str] = None,
        name: Optional[str] = None,
        source: str = TransactionSource.CUSTOM,
        attributes: Optional[dict[str, Any]] = None,
        only_as_child_span: bool = False,
        parent_span: Optional[Span] = None,
        otel_span: Optional[OtelSpan] = None,
        span: Optional[Span] = None,
    ) -> None:
        """
        If otel_span is passed explicitly, just acts as a proxy.

        If span is passed explicitly, use it. The only purpose of this param
        is backwards compatibility with start_transaction(transaction=...).

        If only_as_child_span is True, just return an INVALID_SPAN
        and avoid instrumentation if there's no active parent span.
        """
        if otel_span is not None:
            self._otel_span = otel_span
        elif span is not None:
            self._otel_span = span._otel_span
        else:
            skip_span = False
            if only_as_child_span and parent_span is None:
                parent_span_context = get_current_span().get_span_context()
                skip_span = (
                    not parent_span_context.is_valid or parent_span_context.is_remote
                )

            origin = origin or DEFAULT_SPAN_ORIGIN
            if not skip_span and _is_span_origin_excluded(origin):
                skip_span = True

            if skip_span:
                self._otel_span = INVALID_SPAN
            else:
                if start_timestamp is not None:
                    # OTel timestamps have nanosecond precision
                    start_timestamp = convert_to_otel_timestamp(start_timestamp)

                span_name = name or description or DEFAULT_SPAN_NAME

                # Prepopulate some attrs so that they're accessible in traces_sampler
                attributes = attributes or {}
                if op is not None:
                    attributes[SentrySpanAttribute.OP] = op
                if source is not None:
                    attributes[SentrySpanAttribute.SOURCE] = source
                if description is not None:
                    attributes[SentrySpanAttribute.DESCRIPTION] = description
                if sampled is not None:
                    attributes[SentrySpanAttribute.CUSTOM_SAMPLED] = sampled

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

                self.origin = origin
                self.description = description
                self.name = span_name

                if status is not None:
                    self.set_status(status)

                self.update_active_thread()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Span):
            return False
        return self._otel_span == other._otel_span

    def __repr__(self) -> str:
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

    def activate(self) -> None:
        ctx = otel_trace.set_span_in_context(self._otel_span)
        # set as the implicit current context
        self._ctx_token = context.attach(ctx)

    def deactivate(self) -> None:
        if hasattr(self, "_ctx_token"):
            context.detach(self._ctx_token)
            del self._ctx_token

    def __enter__(self) -> Span:
        self.activate()
        return self

    def __exit__(
        self, ty: Optional[Any], value: Optional[Any], tb: Optional[Any]
    ) -> None:
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
        self.deactivate()

    @property
    def description(self) -> Optional[str]:
        return self.get_attribute(SentrySpanAttribute.DESCRIPTION)

    @description.setter
    def description(self, value: Optional[str]) -> None:
        self.set_attribute(SentrySpanAttribute.DESCRIPTION, value)

    @property
    def origin(self) -> Optional[str]:
        return self.get_attribute(SentrySpanAttribute.ORIGIN)

    @origin.setter
    def origin(self, value: Optional[str]) -> None:
        self.set_attribute(SentrySpanAttribute.ORIGIN, value)

    @property
    def root_span(self) -> Optional[Span]:
        root_otel_span: Optional[OtelSpan] = get_sentry_meta(
            self._otel_span, "root_span"
        )
        return Span(otel_span=root_otel_span) if root_otel_span else None

    @property
    def is_root_span(self) -> bool:
        return self.root_span == self

    @property
    def parent_span_id(self) -> Optional[str]:
        if (
            not isinstance(self._otel_span, ReadableSpan)
            or self._otel_span.parent is None
        ):
            return None
        return format_span_id(self._otel_span.parent.span_id)

    @property
    def trace_id(self) -> str:
        return format_trace_id(self._otel_span.get_span_context().trace_id)

    @property
    def span_id(self) -> str:
        return format_span_id(self._otel_span.get_span_context().span_id)

    @property
    def is_valid(self) -> bool:
        return self._otel_span.get_span_context().is_valid and isinstance(
            self._otel_span, ReadableSpan
        )

    @property
    def sampled(self) -> Optional[bool]:
        return self._otel_span.get_span_context().trace_flags.sampled

    @property
    def sample_rate(self) -> Optional[float]:
        sample_rate = self._otel_span.get_span_context().trace_state.get(
            TRACESTATE_SAMPLE_RATE_KEY
        )
        return float(sample_rate) if sample_rate is not None else None

    @property
    def op(self) -> Optional[str]:
        return self.get_attribute(SentrySpanAttribute.OP)

    @op.setter
    def op(self, value: Optional[str]) -> None:
        self.set_attribute(SentrySpanAttribute.OP, value)

    @property
    def name(self) -> Optional[str]:
        return self.get_attribute(SentrySpanAttribute.NAME)

    @name.setter
    def name(self, value: str) -> None:
        self._otel_span.update_name(value)
        self.set_attribute(SentrySpanAttribute.NAME, value)

    @property
    def source(self) -> str:
        return (
            self.get_attribute(SentrySpanAttribute.SOURCE) or TransactionSource.CUSTOM
        )

    @source.setter
    def source(self, value: str) -> None:
        self.set_attribute(SentrySpanAttribute.SOURCE, value)

    @property
    def start_timestamp(self) -> Optional[datetime]:
        if not isinstance(self._otel_span, ReadableSpan):
            return None

        start_time = self._otel_span.start_time
        if start_time is None:
            return None

        return convert_from_otel_timestamp(start_time)

    @property
    def timestamp(self) -> Optional[datetime]:
        if not isinstance(self._otel_span, ReadableSpan):
            return None

        end_time = self._otel_span.end_time
        if end_time is None:
            return None

        return convert_from_otel_timestamp(end_time)

    def start_child(self, **kwargs: Any) -> Span:
        return Span(parent_span=self, **kwargs)

    def iter_headers(self) -> Iterator[Tuple[str, str]]:
        yield SENTRY_TRACE_HEADER_NAME, self.to_traceparent()
        yield BAGGAGE_HEADER_NAME, serialize_trace_state(self.trace_state)

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

    @property
    def trace_state(self) -> TraceState:
        return get_trace_state(self._otel_span)

    def to_baggage(self) -> Baggage:
        return self.get_baggage()

    def get_baggage(self) -> Baggage:
        return baggage_from_trace_state(self.trace_state)

    def set_tag(self, key: str, value: Any) -> None:
        self.set_attribute(f"{SentrySpanAttribute.TAG}.{key}", value)

    def set_data(self, key: str, value: Any) -> None:
        warnings.warn(
            "`Span.set_data` is deprecated. Please use `Span.set_attribute` instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        # TODO-neel-potel we cannot add dicts here
        self.set_attribute(key, value)

    def get_attribute(self, name: str) -> Optional[Any]:
        if (
            not isinstance(self._otel_span, ReadableSpan)
            or not self._otel_span.attributes
        ):
            return None
        return self._otel_span.attributes.get(name)

    def set_attribute(self, key: str, value: Any) -> None:
        # otel doesn't support None as values, preferring to not set the key
        # at all instead
        if value is None:
            return
        serialized_value = _serialize_span_attribute(value)
        if serialized_value is None:
            return

        self._otel_span.set_attribute(key, serialized_value)

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        for key, value in attributes.items():
            self.set_attribute(key, value)

    @property
    def status(self) -> Optional[str]:
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

    def set_status(self, status: str) -> None:
        if status == SPANSTATUS.OK:
            otel_status = StatusCode.OK
            otel_description = None
        else:
            otel_status = StatusCode.ERROR
            otel_description = status

        if _OTEL_VERSION is None or _OTEL_VERSION >= (1, 12, 0):
            self._otel_span.set_status(otel_status, otel_description)
        else:
            self._otel_span.set_status(Status(otel_status, otel_description))

    def set_thread(self, thread_id: Optional[int], thread_name: Optional[str]) -> None:
        if thread_id is not None:
            self.set_attribute(SPANDATA.THREAD_ID, str(thread_id))

            if thread_name is not None:
                self.set_attribute(SPANDATA.THREAD_NAME, thread_name)

    def update_active_thread(self) -> None:
        thread_id, thread_name = get_current_thread_meta()
        self.set_thread(thread_id, thread_name)

    def set_http_status(self, http_status: int) -> None:
        self.set_attribute(SPANDATA.HTTP_STATUS_CODE, http_status)
        self.set_status(get_span_status_from_http_code(http_status))

    def is_success(self) -> bool:
        return self.status == SPANSTATUS.OK

    def finish(self, end_timestamp: Optional[Union[float, datetime]] = None) -> None:
        if end_timestamp is not None:
            self._otel_span.end(convert_to_otel_timestamp(end_timestamp))
        else:
            self._otel_span.end()

    def to_json(self) -> dict[str, Any]:
        """
        Only meant for testing. Not used internally anymore.
        """
        if not isinstance(self._otel_span, ReadableSpan):
            return {}
        return json.loads(self._otel_span.to_json())

    def get_trace_context(self) -> dict[str, Any]:
        if not isinstance(self._otel_span, ReadableSpan):
            return {}

        return get_trace_context(self._otel_span)

    def set_context(self, key: str, value: Any) -> None:
        # TODO-neel-potel we cannot add dicts here

        self.set_attribute(f"{SentrySpanAttribute.CONTEXT}.{key}", value)

    def set_flag(self, flag: str, value: bool) -> None:
        flag_count = self.get_attribute("_flag.count") or 0
        if flag_count < _FLAGS_CAPACITY:
            self.set_attribute(f"flag.evaluation.{flag}", value)
            self.set_attribute("_flag.count", flag_count + 1)


# TODO-neel-potel add deprecation
Transaction = Span


if TYPE_CHECKING:

    @overload
    def trace(func: None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
        pass

    @overload
    def trace(func: Callable[P, R]) -> Callable[P, R]:
        pass


def trace(
    func: Optional[Callable[P, R]] = None,
) -> Union[Callable[P, R], Callable[[Callable[P, R]], Callable[P, R]]]:
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
