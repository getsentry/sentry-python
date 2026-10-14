import gc

import pytest

import sentry_sdk
from sentry_sdk import start_transaction
from sentry_sdk.integrations.gc import GcIntegration


class Node:
    pass


def create_garbage_cycles(count):
    for _ in range(count):
        a, b = Node(), Node()
        a.other = b
        b.other = a


@pytest.fixture(autouse=True)
def no_automatic_gc():
    # Automatic collections can happen at any point during a test and would
    # create additional spans. Explicit gc.collect() calls still work.
    gc.collect()
    gc.disable()
    yield
    gc.enable()


def collect_in_parent_span(span_streaming, capture_events, capture_items, collect):
    """
    Runs `collect` inside a parent span and returns (parent_span_id, gc_spans).
    The spans are normalized to (name, op, origin, parent_span_id, data).
    """
    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="parent") as parent:
            collect()
        sentry_sdk.flush()

        gc_spans = [
            (
                item.payload["name"],
                item.payload["attributes"]["sentry.op"],
                item.payload["attributes"]["sentry.origin"],
                item.payload.get("parent_span_id"),
                item.payload["attributes"],
            )
            for item in items
            if item.payload["attributes"].get("sentry.op") == "gc"
        ]
        return parent.span_id, gc_spans

    events = capture_events()
    with start_transaction(name="parent") as transaction:
        collect()

    (event,) = events
    gc_spans = [
        (
            span["description"],
            span["op"],
            span["origin"],
            span["parent_span_id"],
            span["data"],
        )
        for span in event["spans"]
        if span["op"] == "gc"
    ]
    return transaction.span_id, gc_spans


@pytest.mark.parametrize("span_streaming", [True, False])
def test_gc_span(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[GcIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    def collect():
        create_garbage_cycles(100)
        gc.collect()

    parent_span_id, gc_spans = collect_in_parent_span(
        span_streaming, capture_events, capture_items, collect
    )

    (span,) = gc_spans
    name, op, origin, span_parent_id, data = span
    assert name == "garbage collection"
    assert op == "gc"
    assert origin == "auto.gc"
    assert span_parent_id == parent_span_id
    assert data["cpython.gc.generation"] == 2
    assert data["cpython.gc.collected_objects"] >= 200
    assert data["cpython.gc.uncollectable_objects"] == 0


@pytest.mark.parametrize("span_streaming", [True, False])
def test_young_generations_skipped_by_default(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[GcIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    def collect():
        gc.collect(0)
        gc.collect(1)

    _, gc_spans = collect_in_parent_span(
        span_streaming, capture_events, capture_items, collect
    )

    assert gc_spans == []


@pytest.mark.parametrize("span_streaming", [True, False])
def test_min_generation(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[GcIntegration(min_generation=1)],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    def collect():
        gc.collect(0)
        gc.collect(1)
        gc.collect(2)

    _, gc_spans = collect_in_parent_span(
        span_streaming, capture_events, capture_items, collect
    )

    assert [span[4]["cpython.gc.generation"] for span in gc_spans] == [1, 2]


@pytest.mark.parametrize("span_streaming", [True, False])
def test_no_span_without_parent(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[GcIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )
    events = capture_events()
    items = capture_items()

    gc.collect()
    sentry_sdk.flush()

    assert events == []
    assert items == []


@pytest.mark.parametrize("span_streaming", [True, False])
def test_no_span_when_integration_disabled(
    sentry_init, capture_events, capture_items, span_streaming
):
    # Make sure the callback is registered, then switch to a client that
    # doesn't have the integration enabled.
    sentry_init(integrations=[GcIntegration()])
    sentry_init(
        default_integrations=False,
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    _, gc_spans = collect_in_parent_span(
        span_streaming, capture_events, capture_items, gc.collect
    )

    assert gc_spans == []


@pytest.mark.parametrize("span_streaming", [True, False])
def test_finalizer_spans_are_not_children_of_gc_span(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[GcIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    class WithFinalizer:
        def __del__(self):
            if span_streaming:
                with sentry_sdk.traces.start_span(
                    name="finalizer", attributes={"sentry.op": "finalizer"}
                ):
                    pass
            else:
                with sentry_sdk.start_span(op="finalizer", name="finalizer"):
                    pass

    def collect():
        a, b = WithFinalizer(), WithFinalizer()
        a.other = b
        b.other = a
        del a, b
        gc.collect()

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="parent") as parent:
            collect()
        sentry_sdk.flush()

        spans = [item.payload for item in items]
        finalizer_spans = [s for s in spans if s["name"] == "finalizer"]
        (gc_span,) = [s for s in spans if s["attributes"].get("sentry.op") == "gc"]
        parent_span_id = parent.span_id
    else:
        events = capture_events()
        with start_transaction(name="parent") as transaction:
            collect()

        (event,) = events
        finalizer_spans = [s for s in event["spans"] if s["op"] == "finalizer"]
        (gc_span,) = [s for s in event["spans"] if s["op"] == "gc"]
        parent_span_id = transaction.span_id

    assert gc_span["parent_span_id"] == parent_span_id
    assert len(finalizer_spans) == 2
    for span in finalizer_spans:
        assert span["parent_span_id"] == parent_span_id
