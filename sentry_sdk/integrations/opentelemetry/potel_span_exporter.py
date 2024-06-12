from opentelemetry.sdk.trace import ReadableSpan  # type: ignore


class PotelSentrySpanExporter:
    """
    A Sentry-specific exporter that converts OpenTelemetry Spans to Sentry Spans & Transactions.
    """

    def __init__(self):
        # type: () -> None
        pass

    def export(self, span):
        # type: (ReadableSpan) -> None
        pass

    def flush(self, timeout_millis):
        # type: (int) -> bool
        return True
