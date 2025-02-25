import re
import pytest
import random
import sys

import sentry_sdk
from sentry_sdk import (
    capture_message,
    continue_trace,
    start_span,
)
from sentry_sdk.consts import SPANSTATUS
from sentry_sdk.transport import Transport
from tests.conftest import SortedBaggage


@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_basic(sentry_init, capture_events, sample_rate):
    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    with start_span(name="hi") as root_span:
        root_span.set_status(SPANSTATUS.OK)
        with pytest.raises(ZeroDivisionError):
            with start_span(op="foo", name="foodesc"):
                1 / 0

        with start_span(op="bar", name="bardesc"):
            pass

    if sample_rate:
        assert len(events) == 1
        event = events[0]

        assert event["transaction"] == "hi"
        assert event["transaction_info"]["source"] == "custom"

        span1, span2 = event["spans"]
        parent_span = event
        assert span1["status"] == "internal_error"
        assert span1["op"] == "foo"
        assert span1["description"] == "foodesc"
        assert span2["status"] == "ok"
        assert span2["op"] == "bar"
        assert span2["description"] == "bardesc"
        assert parent_span["transaction"] == "hi"
        assert "status" not in event.get("tags", {})
        assert event["contexts"]["trace"]["status"] == "ok"
    else:
        assert not events


@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_continue_trace(sentry_init, capture_envelopes, sample_rate):  # noqa:N803
    """
    Ensure data is actually passed along via headers, and that they are read
    correctly.
    """
    sentry_init(traces_sample_rate=sample_rate)
    envelopes = capture_envelopes()

    # make a parent transaction (normally this would be in a different service)
    with start_span(name="hi"):
        with start_span(name="inner") as old_span:
            headers = dict(old_span.iter_headers())
            assert headers["sentry-trace"]
            assert headers["baggage"]

    # child transaction, to prove that we can read 'sentry-trace' header data correctly
    with continue_trace(headers):
        with start_span(name="WRONG") as child_root_span:
            assert child_root_span is not None
            assert child_root_span.sampled == (sample_rate == 1.0)
            if child_root_span.sampled:
                assert child_root_span.parent_span_id == old_span.span_id
            assert child_root_span.trace_id == old_span.trace_id
            assert child_root_span.span_id != old_span.span_id

            baggage = child_root_span.get_baggage()
            assert baggage.serialize() == SortedBaggage(headers["baggage"])

            # change the transaction name from "WRONG" to make sure the change
            # is reflected in the final data
            sentry_sdk.get_current_scope().set_transaction_name("ho")
            # to show that the captured message will be tagged with the trace id
            # (since it happens while the transaction is open)
            capture_message("hello")

    # in this case the child transaction won't be captured
    # but message follows twp spec
    if sample_rate == 0.0:
        (message,) = envelopes
        message_payload = message.get_event()
        assert message_payload["transaction"] == "ho"
        assert (
            child_root_span.trace_id == message_payload["contexts"]["trace"]["trace_id"]
        )
    else:
        trace1, message, trace2 = envelopes
        trace1_payload = trace1.get_transaction_event()
        message_payload = message.get_event()
        trace2_payload = trace2.get_transaction_event()

        assert trace1_payload["transaction"] == "hi"
        assert trace2_payload["transaction"] == "ho"

        assert (
            trace1_payload["contexts"]["trace"]["trace_id"]
            == trace2_payload["contexts"]["trace"]["trace_id"]
            == child_root_span.trace_id
            == message_payload["contexts"]["trace"]["trace_id"]
        )

        assert trace2.headers["trace"] == baggage.dynamic_sampling_context()

    assert message_payload["message"] == "hello"


@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_propagate_traces_deprecation_warning(sentry_init, sample_rate):
    sentry_init(traces_sample_rate=sample_rate, propagate_traces=False)

    with start_span(name="hi"):
        with start_span() as old_span:
            with pytest.warns(DeprecationWarning):
                dict(
                    sentry_sdk.get_current_scope().iter_trace_propagation_headers(
                        old_span
                    )
                )


@pytest.mark.parametrize("sample_rate", [0.5, 1.0])
def test_dynamic_sampling_head_sdk_creates_dsc(
    sentry_init,
    capture_envelopes,
    sample_rate,
    monkeypatch,
):
    sentry_init(traces_sample_rate=sample_rate, release="foo")
    envelopes = capture_envelopes()

    # make sure transaction is sampled for both cases
    monkeypatch.setattr(random, "random", lambda: 0.1)

    with continue_trace({}):
        with start_span(name="Head SDK tx"):
            with start_span(op="foo", name="foodesc") as span:
                baggage = span.get_baggage()

    trace_id = span.trace_id

    assert baggage
    assert baggage.third_party_items == ""
    assert baggage.sentry_items == {
        "environment": "production",
        "release": "foo",
        "sample_rate": str(sample_rate),
        "sampled": "true" if span.sampled else "false",
        "transaction": "Head SDK tx",
        "trace_id": trace_id,
    }

    expected_baggage = (
        "sentry-trace_id=%s,"
        "sentry-environment=production,"
        "sentry-release=foo,"
        "sentry-transaction=Head%%20SDK%%20tx,"
        "sentry-sample_rate=%s,"
        "sentry-sampled=%s"
        % (trace_id, sample_rate, "true" if span.sampled else "false")
    )
    assert baggage.serialize() == SortedBaggage(expected_baggage)

    (envelope,) = envelopes
    assert envelope.headers["trace"] == baggage.dynamic_sampling_context()
    assert envelope.headers["trace"] == {
        "environment": "production",
        "release": "foo",
        "sample_rate": str(sample_rate),
        "sampled": "true" if span.sampled else "false",
        "transaction": "Head SDK tx",
        "trace_id": trace_id,
    }


def test_transactions_do_not_go_through_before_send(sentry_init, capture_events):
    def before_send(event, hint):
        raise RuntimeError("should not be called")

    sentry_init(traces_sample_rate=1.0, before_send=before_send)
    events = capture_events()

    with start_span(name="/"):
        pass

    assert len(events) == 1


def test_start_span_after_finish(sentry_init, capture_events):
    class CustomTransport(Transport):
        def capture_envelope(self, envelope):
            pass

        def capture_event(self, event):
            start_span(op="toolate", name="justdont")
            pass

    sentry_init(traces_sample_rate=1, transport=CustomTransport())
    events = capture_events()

    with start_span(name="hi"):
        with start_span(op="bar", name="bardesc"):
            pass

    assert len(events) == 1


def test_trace_propagation_meta_head_sdk(sentry_init):
    sentry_init(traces_sample_rate=1.0, release="foo")

    meta = None
    span = None

    with continue_trace({}):
        with start_span(name="Head SDK tx") as root_span:
            with start_span(op="foo", name="foodesc") as current_span:
                span = current_span
                meta = sentry_sdk.get_current_scope().trace_propagation_meta()

    ind = meta.find(">") + 1
    sentry_trace, baggage = meta[:ind], meta[ind:]

    assert 'meta name="sentry-trace"' in sentry_trace
    sentry_trace_content = re.findall('content="([^"]*)"', sentry_trace)[0]
    assert sentry_trace_content == span.to_traceparent()

    assert 'meta name="baggage"' in baggage
    baggage_content = re.findall('content="([^"]*)"', baggage)[0]
    assert baggage_content == root_span.get_baggage().serialize()


@pytest.mark.parametrize(
    "exception_cls,exception_value",
    [
        (SystemExit, 0),
    ],
)
def test_non_error_exceptions(
    sentry_init, capture_events, exception_cls, exception_value
):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(name="hi") as root_span:
        root_span.set_status(SPANSTATUS.OK)
        with pytest.raises(exception_cls):
            with start_span(op="foo", name="foodesc"):
                raise exception_cls(exception_value)

    assert len(events) == 1
    event = events[0]

    span = event["spans"][0]
    assert "status" not in span.get("tags", {})
    assert "status" not in event.get("tags", {})
    assert event["contexts"]["trace"]["status"] == "ok"


@pytest.mark.parametrize("exception_value", [None, 0, False])
def test_good_sysexit_doesnt_fail_transaction(
    sentry_init, capture_events, exception_value
):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(name="hi") as span:
        span.set_status(SPANSTATUS.OK)
        with pytest.raises(SystemExit):
            with start_span(op="foo", name="foodesc"):
                if exception_value is not False:
                    sys.exit(exception_value)
                else:
                    sys.exit()

    assert len(events) == 1
    event = events[0]

    span = event["spans"][0]
    assert "status" not in span.get("tags", {})
    assert "status" not in event.get("tags", {})
    assert event["contexts"]["trace"]["status"] == "ok"
