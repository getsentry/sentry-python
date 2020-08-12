import weakref
import gc

import pytest

from sentry_sdk import (
    capture_message,
    configure_scope,
    Hub,
    start_span,
    start_transaction,
)
from sentry_sdk.tracing import Span, Transaction


@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
@pytest.mark.parametrize("num_spans", [1, 900])
@pytest.mark.parametrize("fast_span_ids", [True, False])
def test_basic(
    sentry_init, capture_events, sample_rate, num_spans, benchmark, fast_span_ids
):
    sentry_init(
        traces_sample_rate=sample_rate, _experiments={"fast_span_ids": fast_span_ids}
    )
    events = capture_events()

    @benchmark
    def run():
        with start_transaction(name="hi") as transaction:
            transaction.set_status("ok")
            with pytest.raises(ZeroDivisionError):
                with start_span(op="foo", description="foodesc"):
                    1 / 0

            for _ in range(num_spans):
                with start_span(op="bar", description="bardesc"):
                    pass

    if sample_rate:
        event = events[0]

        assert len(event["spans"]) == num_spans + 1
        span1, span2 = event["spans"][:2]
        parent_span = event
        assert span1["tags"]["status"] == "internal_error"
        assert span1["op"] == "foo"
        assert span1["description"] == "foodesc"
        assert "status" not in span2.get("tags", {})
        assert span2["op"] == "bar"
        assert span2["description"] == "bardesc"
        assert parent_span["transaction"] == "hi"
        assert "status" not in event["tags"]
        assert event["contexts"]["trace"]["status"] == "ok"
    else:
        assert not events


def test_start_span_to_start_transaction(sentry_init, capture_events):
    # XXX: this only exists for backwards compatibility with code before
    # Transaction / start_transaction were introduced.
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(transaction="/1/"):
        pass

    with start_span(Span(transaction="/2/")):
        pass

    assert len(events) == 2
    assert events[0]["transaction"] == "/1/"
    assert events[1]["transaction"] == "/2/"


@pytest.mark.parametrize("sampled", [True, False, None])
def test_continue_from_headers(sentry_init, capture_events, sampled):
    sentry_init(traces_sample_rate=1.0, traceparent_v2=True)
    events = capture_events()

    with start_transaction(name="hi"):
        with start_span() as old_span:
            old_span.sampled = sampled
            headers = dict(Hub.current.iter_trace_propagation_headers())

    header = headers["sentry-trace"]
    if sampled is True:
        assert header.endswith("-1")
    if sampled is False:
        assert header.endswith("-0")
    if sampled is None:
        assert header.endswith("-")

    transaction = Transaction.continue_from_headers(headers, name="WRONG")
    assert transaction is not None
    assert transaction.sampled == sampled
    assert transaction.trace_id == old_span.trace_id
    assert transaction.same_process_as_parent is False
    assert transaction.parent_span_id == old_span.span_id
    assert transaction.span_id != old_span.span_id

    with start_transaction(transaction):
        with configure_scope() as scope:
            scope.transaction = "ho"
        capture_message("hello")

    if sampled is False:
        trace1, message = events

        assert trace1["transaction"] == "hi"
    else:
        trace1, message, trace2 = events

        assert trace1["transaction"] == "hi"
        assert trace2["transaction"] == "ho"

        assert (
            trace1["contexts"]["trace"]["trace_id"]
            == trace2["contexts"]["trace"]["trace_id"]
            == transaction.trace_id
            == message["contexts"]["trace"]["trace_id"]
        )

    assert message["message"] == "hello"


def test_sampling_decided_only_for_transactions(sentry_init, capture_events):
    sentry_init(traces_sample_rate=0.5)

    with start_transaction(name="hi") as transaction:
        assert transaction.sampled is not None

        with start_span() as span:
            assert span.sampled == transaction.sampled

    with start_span() as span:
        assert span.sampled is None


@pytest.mark.parametrize(
    "args,expected_refcount",
    [({"traces_sample_rate": 1.0}, 100), ({"traces_sample_rate": 0.0}, 0)],
)
def test_memory_usage(sentry_init, capture_events, args, expected_refcount):
    sentry_init(**args)

    references = weakref.WeakSet()

    with start_transaction(name="hi"):
        for i in range(100):
            with start_span(op="helloworld", description="hi {}".format(i)) as span:

                def foo():
                    pass

                references.add(foo)
                span.set_tag("foo", foo)
                pass

        del foo
        del span

        # required only for pypy (cpython frees immediately)
        gc.collect()

        assert len(references) == expected_refcount


def test_span_trimming(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, _experiments={"max_spans": 3})
    events = capture_events()

    with start_transaction(name="hi"):
        for i in range(10):
            with start_span(op="foo{}".format(i)):
                pass

    (event,) = events
    span1, span2 = event["spans"]
    assert span1["op"] == "foo0"
    assert span2["op"] == "foo1"


def test_nested_transaction_sampling_override():
    with start_transaction(name="outer", sampled=True) as outer_transaction:
        assert outer_transaction.sampled is True
        with start_transaction(name="inner", sampled=False) as inner_transaction:
            assert inner_transaction.sampled is False
        assert outer_transaction.sampled is True


def test_transaction_method_signature(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with pytest.raises(TypeError):
        start_span(name="foo")
    assert len(events) == 0

    with start_transaction() as transaction:
        pass
    assert transaction.name == "<unlabeled transaction>"
    assert len(events) == 1

    with start_transaction() as transaction:
        transaction.name = "name-known-after-transaction-started"
    assert len(events) == 2

    with start_transaction(name="a"):
        pass
    assert len(events) == 3

    with start_transaction(Transaction(name="c")):
        pass
    assert len(events) == 4


def test_no_double_sampling(sentry_init, capture_events):
    # Transactions should not be subject to the global/error sample rate.
    # Only the traces_sample_rate should apply.
    sentry_init(traces_sample_rate=1.0, sample_rate=0.0)
    events = capture_events()

    with start_transaction(name="/"):
        pass

    assert len(events) == 1


def test_transactions_do_not_go_through_before_send(sentry_init, capture_events):
    def before_send(event, hint):
        raise RuntimeError("should not be called")

    sentry_init(traces_sample_rate=1.0, before_send=before_send)
    events = capture_events()

    with start_transaction(name="/"):
        pass

    assert len(events) == 1


def test_get_transaction_from_scope(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction(name="/"):
        with start_span(op="child-span"):
            with start_span(op="child-child-span"):
                scope = Hub.current.scope
                assert scope.span.op == "child-child-span"
                assert scope.transaction.name == "/"

    assert len(events) == 1
