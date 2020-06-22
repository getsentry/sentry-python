import weakref
import gc

import pytest

from sentry_sdk import (
    Hub,
    capture_message,
    start_span,
    start_transaction,
    configure_scope,
)
from sentry_sdk.tracing import Transaction


@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_basic(sentry_init, capture_events, sample_rate):
    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    with start_transaction(name="hi") as transaction:
        transaction.set_status("ok")
        with pytest.raises(ZeroDivisionError):
            with start_span(op="foo", description="foodesc"):
                1 / 0

        with start_span(op="bar", description="bardesc"):
            pass

    if sample_rate:
        (event,) = events

        span1, span2 = event["spans"]
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


def test_nested_spans_in_scope(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    # XXX: nesting of spans may not work when async code is involved (data race
    # accessing scope._span)
    with start_transaction(name="/status/") as transaction:
        assert Hub.current.scope._span is transaction
        with start_span(op="check1", description="desc1") as span1:
            assert Hub.current.scope._span is span1
            with start_span(op="check1_1", description="desc1_1") as span1_1:
                assert Hub.current.scope._span is span1_1
            assert Hub.current.scope._span is span1
        with start_span(op="check2", description="desc2") as span2:
            assert Hub.current.scope._span is span2
        assert Hub.current.scope._span is transaction
        transaction.set_status("ok")

    assert len(events) == 1
    assert len(events[0]["spans"]) == 3
