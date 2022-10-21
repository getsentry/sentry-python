from opentelemetry.sdk.trace import SpanProcessor

from sentry_sdk.hub import Hub
from sentry_sdk.tracing import Transaction


class SentrySpanProcessor(SpanProcessor):
    otel_span_map = {}

    def on_start(self, otel_span, parent_context=None):
        print(f"[SENTRY/OTEL] in on_start {otel_span}")
        hub = Hub.current
        sentry_span = None
        parent_sentry_span = hub.scope.span

        if parent_sentry_span:
            sentry_span = parent_sentry_span.start_child(op=otel_span.name)
        else:
            # TODO: add the baggagae sentry-trace stuff to transaction
            sentry_span = hub.start_transaction(name=otel_span.name)

        hub.scope.span = sentry_span
        self.otel_span_map[otel_span.context.span_id] = (
            sentry_span,
            parent_sentry_span,
        )

    def on_end(self, otel_span):
        print(f"[SENTRY/OTEL] in on_end {otel_span}")
        hub = Hub.current
        sentry_span, parent_sentry_span = self.otel_span_map[otel_span.context.span_id]

        if not sentry_span:
            return

        sentry_span.op = otel_span.name
        if isinstance(sentry_span, Transaction):
            hub.scope.set_transaction_name(otel_span.name)

        for key in otel_span.attributes:
            val = otel_span.attributes[key]
            sentry_span.set_data(key, val)

            if key == "db.statement":
                sentry_span.description = val

        sentry_span.finish()

        if parent_sentry_span:
            hub.scope.span = parent_sentry_span
