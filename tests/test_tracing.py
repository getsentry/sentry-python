import weakref
import gc

import pytest

from sentry_sdk import Hub, capture_message
from sentry_sdk.tracing import Span


@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_basic(sentry_init, capture_events, sample_rate):
    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    with Hub.current.start_span(transaction="hi"):
        with pytest.raises(ZeroDivisionError):
            with Hub.current.start_span(op="foo", description="foodesc"):
                1 / 0

        with Hub.current.start_span(op="bar", description="bardesc"):
            pass

    if sample_rate:
        event, = events

        span1, span2 = event["spans"]
        parent_span = event
        assert span1["tags"]["status"] == "failure"
        assert span1["op"] == "foo"
        assert span1["description"] == "foodesc"
        assert "status" not in span2["tags"]
        assert span2["op"] == "bar"
        assert span2["description"] == "bardesc"
        assert parent_span["transaction"] == "hi"
    else:
        assert not events


@pytest.mark.parametrize("sampled", [True, False, None])
def test_continue_from_headers(sentry_init, capture_events, sampled):
    sentry_init(traces_sample_rate=1.0, traceparent_v2=True)
    events = capture_events()

    with Hub.current.start_span(transaction="hi"):
        with Hub.current.start_span() as old_span:
            old_span.sampled = sampled
            headers = dict(Hub.current.iter_trace_propagation_headers())

    header = headers["sentry-trace"]
    if sampled is True:
        assert header.endswith("-1")
    if sampled is False:
        assert header.endswith("-0")
    if sampled is None:
        assert header.endswith("-")

    span = Span.continue_from_headers(headers)
    span.transaction = "WRONG"
    assert span is not None
    assert span.sampled == sampled
    assert span.trace_id == old_span.trace_id

    with Hub.current.start_span(span):
        with Hub.current.configure_scope() as scope:
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
            == span.trace_id
            == message["contexts"]["trace"]["trace_id"]
        )

    assert message["message"] == "hello"


def test_sampling_decided_only_for_transactions(sentry_init, capture_events):
    sentry_init(traces_sample_rate=0.5)

    with Hub.current.start_span(transaction="hi") as trace:
        assert trace.sampled is not None

        with Hub.current.start_span() as span:
            assert span.sampled == trace.sampled

    with Hub.current.start_span() as span:
        assert span.sampled is None


@pytest.mark.parametrize(
    "args,expected_refcount",
    [({"traces_sample_rate": 1.0}, 100), ({"traces_sample_rate": 0.0}, 0)],
)
def test_memory_usage(sentry_init, capture_events, args, expected_refcount):
    sentry_init(**args)

    references = weakref.WeakSet()

    with Hub.current.start_span(transaction="hi"):
        for i in range(100):
            with Hub.current.start_span(
                op="helloworld", description="hi {}".format(i)
            ) as span:

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
