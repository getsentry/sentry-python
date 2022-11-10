from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.semconv.trace import SpanAttributes

from sentry_sdk.hub import Hub
from sentry_sdk.tracing import Transaction


class SentrySpanProcessor(SpanProcessor):
    """
    Converts OTel spans into Sentry spans so they can be sent to the Sentry backend.
    """

    otel_span_map = {}  # The mapping from otel span ids to sentry spans

    def on_start(self, otel_span, parent_context=None):
        hub = Hub.current
        if not hub:
            return

        # TODO: check for isSentryRequest and if sdk is initialized...

        span_id = otel_span.context.span_id
        parent_span_id = otel_span.parent.span_id if otel_span.parent else None
        trace_id = otel_span.context.trace_id

        parent_sentry_span = (
            self.otel_span_map[parent_span_id] if parent_span_id else None
        )

        sentry_span = None
        if parent_sentry_span:
            sentry_span = parent_sentry_span.start_child(
                span_id=span_id,
                description=otel_span.name,
                instrumenter="sentry",
            )
        else:
            # TODO: add the baggagae sentry-trace stuff to transaction
            sentry_span = hub.start_transaction(
                name=otel_span.name,
                span_id=span_id,
                parent_span_id=parent_span_id,
                trace_id=trace_id,
                baggage={},  # TODO: add baggage
                # TODO: check if we can set start_timestamp
                instrumenter="sentry",
            )

        self.otel_span_map[span_id] = sentry_span

    def on_end(self, otel_span):
        hub = Hub.current
        if not hub:
            return

        scope = hub.scope
        if not scope:
            return

        span_id = otel_span.context.span_id
        sentry_span = self.otel_span_map.pop(span_id)
        if not sentry_span:
            return

        sentry_span.op = otel_span.name

        if isinstance(sentry_span, Transaction):
            scope.set_transaction_name(otel_span.name)
            # TODO: set "otel" context scope.set_context(...)
        else:
            self._update_span_with_otel_data(sentry_span, otel_span)

        sentry_span.finish()

    def _update_span_with_otel_data(self, sentry_span, otel_span):
        for key in otel_span.attributes:
            val = otel_span.attributes[key]
            sentry_span.set_data(key, val)

        op = otel_span.name
        description = otel_span.name

        http_method = otel_span.attributes.get(SpanAttributes.HTTP_METHOD, None)
        db_query = otel_span.attributes.get(SpanAttributes.DB_SYSTEM, None)

        if http_method:
            op = "http.{}".format(http_method)
            description = http_method

            peer_name = otel_span.attributes.get(SpanAttributes.NET_PEER_NAME, None)
            if peer_name:
                description += " {}".format(peer_name)

            target = otel_span.attributes.get(SpanAttributes.HTTP_TARGET, None)
            if target:
                description += " {}".format(target)

            status_code = otel_span.attributes.get(
                SpanAttributes.HTTP_STATUS_CODE, None
            )
            if status_code:
                sentry_span.set_http_status(status_code)

        elif db_query:
            op = "db"
            statement = otel_span.attributes.get(SpanAttributes.DB_STATEMENT, None)
            if statement:
                description = statement

        sentry_span.op = op
        sentry_span.description = description
