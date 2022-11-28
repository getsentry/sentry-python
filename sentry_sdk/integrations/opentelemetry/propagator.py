from opentelemetry import trace
from opentelemetry.context import (  # type: ignore
    Context,
    create_key,
    get_current,
    set_value,
)
from opentelemetry.propagators.textmap import (  # type: ignore
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.trace import (  # type: ignore
    TraceFlags,
    NonRecordingSpan,
    SpanContext,
)

from sentry_sdk.tracing import SENTRY_TRACE_HEADER_NAME, Transaction
from sentry_sdk.tracing_utils import Baggage
from sentry_sdk._types import MYPY

if MYPY:
    from typing import Optional
    from typing import Set


BAGGAGE_HEADER_NAME = "baggage"


SENTRY_TRACE_KEY = create_key("sentry-trace")
SENTRY_BAGGAGE_KEY = create_key("sentry-baggage")


class SentryPropagator(TextMapPropagator):  # type: ignore
    def extract(self, carrier, context=None, getter=default_getter):
        # type: (CarrierT, Optional[Context], Getter) -> Context
        if context is None:
            context = get_current()

        sentry_trace = getter.get(carrier, SENTRY_TRACE_HEADER_NAME)
        if not sentry_trace:
            return context

        sentry_trace_data = Transaction.extract_sentry_trace(sentry_trace[0])

        context = set_value(SENTRY_TRACE_KEY, sentry_trace_data, context)

        trace_id, span_id, _ = sentry_trace_data

        span_context = SpanContext(
            trace_id=int(trace_id, 16),
            span_id=int(span_id, 16),
            # we simulate a sampled trace on the otel side and leave the sampling to sentry
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            is_remote=True,
        )

        baggage_header = getter.get(carrier, BAGGAGE_HEADER_NAME)

        if baggage_header:
            baggage = Baggage.from_incoming_header(baggage_header[0])
        else:
            # If there's an incoming sentry-trace but no incoming baggage header,
            # for instance in traces coming from older SDKs,
            # baggage will be empty and frozen and won't be populated as head SDK.
            baggage = Baggage(sentry_items={})

        baggage.freeze()
        context = set_value(SENTRY_BAGGAGE_KEY, baggage, context)

        span = NonRecordingSpan(span_context)
        modified_context = trace.set_span_in_context(span, context)
        return modified_context

    def inject(self, carrier, context=None, setter=default_setter):
        # type: (CarrierT, Optional[Context], Setter) -> None
        if context is None:
            context = get_current()

        current_span = trace.get_current_span(context)
        span_id = trace.format_span_id(current_span.context.span_id)

        from sentry_sdk.integrations.opentelemetry.span_processor import (
            SentrySpanProcessor,
        )

        span_map = SentrySpanProcessor().otel_span_map
        sentry_span = span_map.get(span_id, None)
        if not sentry_span:
            return

        setter.set(carrier, SENTRY_TRACE_HEADER_NAME, sentry_span.to_traceparent())

        baggage = sentry_span.get_baggage()
        if baggage:
            setter.set(carrier, BAGGAGE_HEADER_NAME, baggage.serialize())

    @property
    def fields(self):
        # type: () -> Set[str]
        return {SENTRY_TRACE_HEADER_NAME, BAGGAGE_HEADER_NAME}
