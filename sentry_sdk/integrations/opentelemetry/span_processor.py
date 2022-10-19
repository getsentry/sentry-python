from opentelemetry.sdk.trace import SpanProcessor


class SentrySpanProcessor(SpanProcessor):
    def on_start(self, span, parent_context=None):
        print(f"[] in on_start {span}")

    def on_end(self, span):
        print(f"~ in on_end {span}")
