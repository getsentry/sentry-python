from opentelemetry.sdk.trace import SpanProcessor

from sentry_sdk.hub import Hub
from sentry_sdk.tracing import Transaction


class SentrySpanProcessor(SpanProcessor):
    otel_span_map = {}

    def on_start(self, otel_span, parent_context=None):
        # print(f"[SENTRY/OTEL] in on_start {otel_span}")
        print(f"######### START SPAN: {otel_span.context.span_id} ({otel_span.name})")
        hub = Hub.current
        print(f"HUB: {hub}")
        if not hub:
            return

        scope = hub.scope
        print(f"SCOPE: {scope}")
        if not scope:
            return

        # TODO: isSentryRequest

        sentry_span = None
        parent_sentry_span = scope.span
        print(f"SPAN: {parent_sentry_span}")

        if parent_sentry_span:
            print(f"######### START CHILD for PARENT: {parent_sentry_span}")
            sentry_span = parent_sentry_span.start_child(
                instrumenter="sentry",
                op="SENTRYOTEL-{}".format(otel_span.name),
            )
            print(f"######### CHILD: {sentry_span}")
        else:
            # TODO: add the baggagae sentry-trace stuff to transaction
            print(
                f"######### START TRANSACTION: {otel_span.context.span_id} ({otel_span.name})"
            )
            sentry_span = hub.start_transaction(
                instrumenter="sentry",
                name="SENTRYOTEL-{}".format(otel_span.name),
            )
            print(f"######### TRANSACTION SPAN: {sentry_span}")

        hub.scope.span = sentry_span
        self.otel_span_map[otel_span.context.span_id] = (
            sentry_span,
            parent_sentry_span,
        )

    def on_end(self, otel_span):
        # print(f"[SENTRY/OTEL] in on_end {otel_span}")
        print(f"######### END SPAN: {otel_span.context.span_id} ({otel_span.name})")

        hub = Hub.current
        print(f"HUB: {hub}")
        if not hub:
            return

        scope = hub.scope
        print(f"SCOPE: {scope}")
        if not scope:
            return

        sentry_span, parent_sentry_span = self.otel_span_map.pop(
            otel_span.context.span_id
        )
        print(f"SPAN: {sentry_span}")
        print(f"PARENT SPAN: {parent_sentry_span}")
        if not sentry_span:
            return

        sentry_span.op = otel_span.name
        if isinstance(sentry_span, Transaction):
            hub.scope.set_transaction_name("SENTRYOTEL-{}".format(otel_span.name))
            # TODO: set otel context
            # hub.scope.set_context(...)

        for key in otel_span.attributes:
            val = otel_span.attributes[key]
            sentry_span.set_data(key, val)

            if key == "db.statement":
                sentry_span.description = val

        sentry_span.finish()

        if parent_sentry_span:
            print(f"SET NEW CURRENT SPAN: {parent_sentry_span}")
            scope.span = parent_sentry_span
