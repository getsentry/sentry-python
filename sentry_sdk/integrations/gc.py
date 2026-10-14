import gc
import sysconfig
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

# The garbage collector of free-threaded builds isn't generational: every
# collection scans all objects, regardless of the generation it reports. This
# is a property of the build, so it also applies if the GIL was re-enabled at
# runtime.
_GC_IS_GENERATIONAL = not sysconfig.get_config_var("Py_GIL_DISABLED")

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
        collections (generation 2) are recorded. Ignored on free-threaded
        builds, where every collection is a full collection.
    """

    identifier = "gc"
    origin = ORIGIN

    def __init__(self, min_generation: int = 2) -> None:
        self.min_generation = min_generation

    @staticmethod
    def setup_once() -> None:
        gc.callbacks.append(_gc_callback)


def _gc_callback(phase: str, info: "Dict[str, Any]") -> None:
    # This runs in the middle of arbitrary user (or SDK) code, so it must never
    # raise.
    with capture_internal_exceptions():
        if phase == "start":
            _start_gc_span(info)
        elif phase == "stop":
            _end_gc_span(info)


def _start_gc_span(info: "Dict[str, Any]") -> None:
    client = sentry_sdk.get_client()
    integration = client.get_integration(GcIntegration)
    if integration is None:
        return

    generation = info["generation"]
    if _GC_IS_GENERATIONAL and generation < integration.min_generation:
        return

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
        span = sentry_sdk.start_span(
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
