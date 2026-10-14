import gc
import threading
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Union

    from sentry_sdk.tracing import Span


ORIGIN = "auto.gc"

# The span of the garbage collection run that is currently in progress on this
# thread. A collection runs synchronously from start to stop in the thread that
# triggered it, and collections can't nest, so there's at most one at a time.
_pending = threading.local()


class GcIntegration(Integration):
    """
    Creates a span for each garbage collection run that happens while a span is
    active.

    :param min_generation: Only collections of this generation or higher are
        recorded. Collections of the younger generations are very frequent and
        usually take well under a millisecond, so by default only full
        collections (generation 2) are recorded.
    """

    identifier = "gc"
    origin = ORIGIN

    def __init__(self, min_generation: int = 2) -> None:
        self.min_generation = min_generation

    @staticmethod
    def setup_once() -> None:
        # gc.callbacks is the interpreter's official hook into garbage
        # collection. The callback is invoked right before ("start") and right
        # after ("stop") every collection.
        gc.callbacks.append(_gc_callback)


def _gc_callback(phase: str, info: "Dict[str, Any]") -> None:
    # This runs in the middle of arbitrary user (or SDK) code, so it must never
    # raise. An exception here would be reported as unraisable on stderr.
    with capture_internal_exceptions():
        if phase == "start":
            _start_gc_span(info)
        elif phase == "stop":
            _end_gc_span(info)


def _start_gc_span(info: "Dict[str, Any]") -> None:
    # This runs on every collection, including the very frequent young
    # generation ones, so bail out as cheaply as possible.
    client = sentry_sdk.get_client()
    integration = client.get_integration(GcIntegration)
    if integration is None:
        return

    generation = info["generation"]
    if generation < integration.min_generation:
        return

    # The span is started without making it the active span: finalizers
    # (__del__, weakref callbacks) run during the collection and might create
    # spans of their own, which shouldn't end up as children of the gc span.
    # We also only ever attach the gc span to an existing span, never start a
    # new trace for it.
    span: "Union[Span, StreamedSpan]"
    if has_span_streaming_enabled(client.options):
        parent = sentry_sdk.traces.get_current_span()
        if parent is None:
            return

        span = sentry_sdk.traces.start_span(
            name="garbage collection",
            attributes={
                "sentry.op": OP.GC,
                "sentry.origin": GcIntegration.origin,
                SPANDATA.CPYTHON_GC_GENERATION: generation,
            },
            active=False,
        )
    else:
        legacy_parent = sentry_sdk.get_current_span()
        if legacy_parent is None:
            return

        span = legacy_parent.start_child(
            op=OP.GC,
            name="garbage collection",
            origin=GcIntegration.origin,
        )
        span.set_data(SPANDATA.CPYTHON_GC_GENERATION, generation)

    _pending.span = span


def _end_gc_span(info: "Dict[str, Any]") -> None:
    span: "Optional[Union[Span, StreamedSpan]]" = getattr(_pending, "span", None)
    if span is None:
        return

    _pending.span = None

    if isinstance(span, StreamedSpan):
        span.set_attribute(SPANDATA.CPYTHON_GC_COLLECTED_OBJECTS, info["collected"])
        span.set_attribute(
            SPANDATA.CPYTHON_GC_UNCOLLECTABLE_OBJECTS, info["uncollectable"]
        )
        span.end()
    else:
        span.set_data(SPANDATA.CPYTHON_GC_COLLECTED_OBJECTS, info["collected"])
        span.set_data(SPANDATA.CPYTHON_GC_UNCOLLECTABLE_OBJECTS, info["uncollectable"])
        span.finish()
