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
from sentry_sdk.transport import Transport
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
        assert len(events) == 1
        event = events[0]

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
@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_continue_from_headers(sentry_init, capture_events, sampled, sample_rate):
    """
    Ensure data is actually passed along via headers, and that they are read
    correctly.
    """
    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    # make a parent transaction (normally this would be in a different service)
    with start_transaction(
        name="hi", sampled=True if sample_rate == 0 else None
    ) as parent_transaction:
        with start_span() as old_span:
            old_span.sampled = sampled
            headers = dict(Hub.current.iter_trace_propagation_headers(old_span))
            tracestate = parent_transaction._sentry_tracestate

    # child transaction, to prove that we can read 'sentry-trace' and
    # `tracestate` header data correctly
    child_transaction = Transaction.continue_from_headers(headers, name="WRONG")
    assert child_transaction is not None
    assert child_transaction.parent_sampled == sampled
    assert child_transaction.trace_id == old_span.trace_id
    assert child_transaction.same_process_as_parent is False
    assert child_transaction.parent_span_id == old_span.span_id
    assert child_transaction.span_id != old_span.span_id
    assert child_transaction._sentry_tracestate == tracestate

    # add child transaction to the scope, to show that the captured message will
    # be tagged with the trace id (since it happens while the transaction is
    # open)
    with start_transaction(child_transaction):
        with configure_scope() as scope:
            # change the transaction name from "WRONG" to make sure the change
            # is reflected in the final data
            scope.transaction = "ho"
        capture_message("hello")

    # in this case the child transaction won't be captured
    if sampled is False or (sample_rate == 0 and sampled is None):
        trace1, message = events

        assert trace1["transaction"] == "hi"
    else:
        trace1, message, trace2 = events

        assert trace1["transaction"] == "hi"
        assert trace2["transaction"] == "ho"

        assert (
            trace1["contexts"]["trace"]["trace_id"]
            == trace2["contexts"]["trace"]["trace_id"]
            == child_transaction.trace_id
            == message["contexts"]["trace"]["trace_id"]
        )

    assert message["message"] == "hello"


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


def test_transactions_do_not_go_through_before_send(sentry_init, capture_events):
    def before_send(event, hint):
        raise RuntimeError("should not be called")

    sentry_init(traces_sample_rate=1.0, before_send=before_send)
    events = capture_events()

    with start_transaction(name="/"):
        pass

    assert len(events) == 1


def test_start_span_after_finish(sentry_init, capture_events):
    class CustomTransport(Transport):
        def capture_envelope(self, envelope):
            pass

        def capture_event(self, event):
            start_span(op="toolate", description="justdont")
            pass

    sentry_init(traces_sample_rate=1, transport=CustomTransport())
    events = capture_events()

    with start_transaction(name="hi"):
        with start_span(op="bar", description="bardesc"):
            pass

    assert len(events) == 1
