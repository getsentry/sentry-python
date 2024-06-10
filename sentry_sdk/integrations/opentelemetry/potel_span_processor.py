from opentelemetry.sdk.trace import SpanProcessor  # type: ignore
from opentelemetry.context import Context  # type: ignore
from opentelemetry.trace import Span  # type: ignore

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
        pass

    def on_start(self, span, parent_context=None):
        # type: (Span, Optional[Context]) -> None
        pass

    def on_end(self, span):
        # type: (Span) -> None
        pass

    # TODO-neel-potel not sure we need a clear like JS
    def shutdown(self):
        # type: () -> None
        pass

    # TODO-neel-potel change default? this is 30 sec
    # TODO-neel-potel call this in client.flush
    def force_flush(self, timeout_millis=30000):
        # type: (int) -> bool
        return True
