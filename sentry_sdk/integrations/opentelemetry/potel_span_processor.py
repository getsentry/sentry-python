from opentelemetry.trace import INVALID_SPAN, get_current_span  # type: ignore
from opentelemetry.context import Context  # type: ignore
from opentelemetry.sdk.trace import Span, ReadableSpan, SpanProcessor  # type: ignore

from sentry_sdk.integrations.opentelemetry.utils import is_sentry_span
from sentry_sdk.integrations.opentelemetry.potel_span_exporter import (
    PotelSentrySpanExporter,
)

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional


class PotelSentrySpanProcessor(SpanProcessor):  # type: ignore
    """
    Converts OTel spans into Sentry spans so they can be sent to the Sentry backend.
    """

    def __new__(cls):
        # type: () -> PotelSentrySpanProcessor
        if not hasattr(cls, "instance"):
            cls.instance = super().__new__(cls)

        return cls.instance

    def __init__(self):
        # type: () -> None
        self._exporter = PotelSentrySpanExporter()

    def on_start(self, span, parent_context=None):
        # type: (Span, Optional[Context]) -> None
        pass
        # if is_sentry_span(span):
        #     return

        # parent_span = get_current_span(parent_context)

        # # TODO-neel-potel check remote logic with propagation and incoming trace later
        # if parent_span != INVALID_SPAN:
        #     # TODO-neel once we add our apis, we might need to store references on the span
        #     # directly, see if we need to do this like JS
        #     pass

    def on_end(self, span):
        # type: (ReadableSpan) -> None
        self._exporter.export(span)

    # TODO-neel-potel not sure we need a clear like JS
    def shutdown(self):
        # type: () -> None
        pass

    # TODO-neel-potel change default? this is 30 sec
    # TODO-neel-potel call this in client.flush
    def force_flush(self, timeout_millis=30000):
        # type: (int) -> bool
        return self._exporter.flush(timeout_millis)
