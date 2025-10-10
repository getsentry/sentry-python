import sentry_sdk
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.scope import add_global_event_processor

try:
    from opentelemetry import trace
except ImportError:
    raise DidNotEnable("opentelemetry is not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional

    from sentry_sdk._types import Event, Hint


class OtlpIntegration(Integration):
    identifier = "otlp"

    @staticmethod
    def setup_once():
        # type: () -> None
        @add_global_event_processor
        def link_trace_context_to_error_event(event, hint):
            # type: (Event, Optional[Hint]) -> Optional[Event]
            integration = sentry_sdk.get_client().get_integration(OtlpIntegration)
            if integration is None:
                return event

            if hasattr(event, "type") and event["type"] == "transaction":
                return event

            otel_span = trace.get_current_span()
            if not otel_span:
                return event

            ctx = otel_span.get_span_context()

            if (
                ctx.trace_id == trace.INVALID_TRACE_ID
                or ctx.span_id == trace.INVALID_SPAN_ID
            ):
                return event

            contexts = event.setdefault("contexts", {})
            contexts.setdefault("trace", {}).update(
                {
                    "trace_id": trace.format_trace_id(ctx.trace_id),
                    "span_id": trace.format_span_id(ctx.span_id),
                    "status": "ok",  # TODO
                }
            )

            return event
