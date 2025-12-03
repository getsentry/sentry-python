import gc
import re
import sys
import weakref
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk import (
    capture_message,
    start_span,
    start_transaction,
    continue_trace,
)
from sentry_sdk.consts import SPANSTATUS
from sentry_sdk.transport import Transport
from tests.conftest import TestTransportWithOptions


@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_basic(sentry_init, capture_events, sample_rate):
    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    with start_transaction(name="hi") as transaction:
        transaction.set_status(SPANSTATUS.OK)
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
        assert span1["tags"]["status"] == "internal_error"
        assert span1["op"] == "foo"
        assert span1["description"] == "foodesc"
        assert "status" not in span2
        assert "status" not in span2.get("tags", {})
        assert span2["op"] == "bar"
        assert span2["description"] == "bardesc"
        assert parent_span["transaction"] == "hi"
        assert "status" not in event["tags"]
        assert event["contexts"]["trace"]["status"] == "ok"
    else:
        assert not events


@pytest.mark.parametrize("parent_sampled", [True, False, None])
@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_continue_trace(sentry_init, capture_envelopes, parent_sampled, sample_rate):
    """
    Ensure data is actually passed along via headers, and that they are read
    correctly.
    """
    sentry_init(traces_sample_rate=sample_rate)
    envelopes = capture_envelopes()

    # make a parent transaction (normally this would be in a different service)
    with start_transaction(name="hi", sampled=True if sample_rate == 0 else None):
        with start_span() as old_span:
            old_span.sampled = parent_sampled
            headers = dict(
                sentry_sdk.get_current_scope().iter_trace_propagation_headers(old_span)
            )
            headers["baggage"] = (
                "other-vendor-value-1=foo;bar;baz, "
                "sentry-trace_id=771a43a4192642f0b136d5159a501700, "
                "sentry-public_key=49d0f7386ad645858ae85020e393bef3, "
                "sentry-sample_rate=0.01337, sentry-user_id=Amelie, "
                "sentry-sample_rand=0.250000, "
                "other-vendor-value-2=foo;bar;"
            )

    # child transaction, to prove that we can read 'sentry-trace' header data correctly
    child_transaction = continue_trace(headers, name="WRONG")
    assert child_transaction is not None
    assert child_transaction.parent_sampled == parent_sampled
    assert child_transaction.trace_id == old_span.trace_id
    assert child_transaction.same_process_as_parent is False
    assert child_transaction.parent_span_id == old_span.span_id
    assert child_transaction.span_id != old_span.span_id

    baggage = child_transaction._baggage
    assert baggage
    assert not baggage.mutable
    assert baggage.sentry_items == {
        "public_key": "49d0f7386ad645858ae85020e393bef3",
        "trace_id": "771a43a4192642f0b136d5159a501700",
        "user_id": "Amelie",
        "sample_rand": "0.250000",
        "sample_rate": "0.01337",
    }

    # add child transaction to the scope, to show that the captured message will
    # be tagged with the trace id (since it happens while the transaction is
    # open)
    with start_transaction(child_transaction):
        # change the transaction name from "WRONG" to make sure the change
        # is reflected in the final data
        sentry_sdk.get_current_scope().transaction = "ho"
        capture_message("hello")

    if parent_sampled is False or (sample_rate == 0 and parent_sampled is None):
        # in this case the child transaction won't be captured
        trace1, message = envelopes
        message_payload = message.get_event()
        trace1_payload = trace1.get_transaction_event()

        assert trace1_payload["transaction"] == "hi"
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
            == child_transaction.trace_id
            == message_payload["contexts"]["trace"]["trace_id"]
        )

        if parent_sampled is not None:
            expected_sample_rate = str(float(parent_sampled))
        else:
            expected_sample_rate = str(sample_rate)

        assert trace2.headers["trace"] == baggage.dynamic_sampling_context()
        assert trace2.headers["trace"] == {
            "public_key": "49d0f7386ad645858ae85020e393bef3",
            "trace_id": "771a43a4192642f0b136d5159a501700",
            "user_id": "Amelie",
            "sample_rand": "0.250000",
            "sample_rate": expected_sample_rate,
        }

    assert message_payload["message"] == "hello"


@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_propagate_traces_deprecation_warning(sentry_init, sample_rate):
    sentry_init(traces_sample_rate=sample_rate, propagate_traces=False)

    with start_transaction(name="hi"):
        with start_span() as old_span:
            with pytest.warns(DeprecationWarning):
                dict(
                    sentry_sdk.get_current_scope().iter_trace_propagation_headers(
                        old_span
                    )
                )


@pytest.mark.parametrize("sample_rate", [0.5, 1.0])
def test_dynamic_sampling_head_sdk_creates_dsc(
    sentry_init, capture_envelopes, sample_rate, monkeypatch
):
    sentry_init(traces_sample_rate=sample_rate, release="foo")
    envelopes = capture_envelopes()

    # make sure transaction is sampled for both cases
    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=250000):
        transaction = continue_trace({}, name="Head SDK tx")

    baggage = transaction._baggage
    assert baggage is None

    with start_transaction(transaction):
        with start_span(op="foo", name="foodesc"):
            pass

    # finish will create a new baggage entry
    baggage = transaction._baggage
    trace_id = transaction.trace_id

    assert baggage
    assert not baggage.mutable
    assert baggage.third_party_items == ""
    assert baggage.sentry_items == {
        "environment": "production",
        "release": "foo",
        "sample_rate": str(sample_rate),
        "sampled": "true" if transaction.sampled else "false",
        "sample_rand": "0.250000",
        "transaction": "Head SDK tx",
        "trace_id": trace_id,
    }

    expected_baggage = (
        "sentry-trace_id=%s,"
        "sentry-sample_rand=0.250000,"
        "sentry-environment=production,"
        "sentry-release=foo,"
        "sentry-transaction=Head%%20SDK%%20tx,"
        "sentry-sample_rate=%s,"
        "sentry-sampled=%s"
        % (trace_id, sample_rate, "true" if transaction.sampled else "false")
    )
    assert baggage.serialize() == expected_baggage

    (envelope,) = envelopes
    assert envelope.headers["trace"] == baggage.dynamic_sampling_context()
    assert envelope.headers["trace"] == {
        "environment": "production",
        "release": "foo",
        "sample_rate": str(sample_rate),
        "sample_rand": "0.250000",
        "sampled": "true" if transaction.sampled else "false",
        "transaction": "Head SDK tx",
        "trace_id": trace_id,
    }


@pytest.mark.parametrize(
    "args,expected_refcount",
    [({"traces_sample_rate": 1.0}, 100), ({"traces_sample_rate": 0.0}, 0)],
)
def test_memory_usage(sentry_init, capture_events, args, expected_refcount):
    sentry_init(**args)

    references = weakref.WeakSet()

    with start_transaction(name="hi"):
        for i in range(100):
            with start_span(op="helloworld", name="hi {}".format(i)) as span:

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
            start_span(op="toolate", name="justdont")
            pass

    sentry_init(traces_sample_rate=1, transport=CustomTransport())
    events = capture_events()

    with start_transaction(name="hi"):
        with start_span(op="bar", name="bardesc"):
            pass

    assert len(events) == 1


def test_trace_propagation_meta_head_sdk(sentry_init):
    sentry_init(traces_sample_rate=1.0, release="foo")

    transaction = continue_trace({}, name="Head SDK tx")
    meta = None
    span = None

    with start_transaction(transaction):
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
    assert baggage_content == transaction.get_baggage().serialize()


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

    with start_transaction(name="hi") as transaction:
        transaction.set_status(SPANSTATUS.OK)
        with pytest.raises(exception_cls):
            with start_span(op="foo", name="foodesc"):
                raise exception_cls(exception_value)

    assert len(events) == 1
    event = events[0]

    span = event["spans"][0]
    assert "status" not in span
    assert "status" not in span.get("tags", {})
    assert "status" not in event["tags"]
    assert event["contexts"]["trace"]["status"] == "ok"


@pytest.mark.parametrize("exception_value", [None, 0, False])
def test_good_sysexit_doesnt_fail_transaction(
    sentry_init, capture_events, exception_value
):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction(name="hi") as transaction:
        transaction.set_status(SPANSTATUS.OK)
        with pytest.raises(SystemExit):
            with start_span(op="foo", name="foodesc"):
                if exception_value is not False:
                    sys.exit(exception_value)
                else:
                    sys.exit()

    assert len(events) == 1
    event = events[0]

    span = event["spans"][0]
    assert "status" not in span
    assert "status" not in span.get("tags", {})
    assert "status" not in event["tags"]
    assert event["contexts"]["trace"]["status"] == "ok"


@pytest.mark.parametrize(
    "strict_trace_continuation,baggage_org_id,dsn_org_id,should_continue_trace",
    (
        (True, "sentry-org_id=1234", "o1234", True),
        (True, "sentry-org_id=1234", "o9999", False),
        (True, "sentry-org_id=9999", "o1234", False),
        (False, "sentry-org_id=1234", "o1234", True),
        (False, "sentry-org_id=9999", "o1234", False),
        (False, "sentry-org_id=1234", "o9999", False),
        (False, "sentry-org_id=1234", "not_org_id", True),
        (False, "", "o1234", True),
    ),
)
def test_continue_trace_strict_trace_continuation(
    sentry_init,
    strict_trace_continuation,
    baggage_org_id,
    dsn_org_id,
    should_continue_trace,
):
    sentry_init(
        dsn=f"https://mysecret@{dsn_org_id}.ingest.sentry.io/12312012",
        strict_trace_continuation=strict_trace_continuation,
        traces_sample_rate=1.0,
        transport=TestTransportWithOptions,
    )

    headers = {
        "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
        "baggage": (
            "other-vendor-value-1=foo;bar;baz, sentry-trace_id=771a43a4192642f0b136d5159a501700, "
            f"{baggage_org_id}, "
            "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
            "sentry-user_id=Am%C3%A9lie, other-vendor-value-2=foo;bar;"
        ),
    }

    transaction = continue_trace(headers, name="strict trace")

    if should_continue_trace:
        assert (
            transaction.trace_id
            == "771a43a4192642f0b136d5159a501700"
            == "771a43a4192642f0b136d5159a501700"
        )
        assert transaction.parent_span_id == "1234567890abcdef"
        assert transaction.parent_sampled
    else:
        assert (
            transaction.trace_id
            != "771a43a4192642f0b136d5159a501700"
            == "771a43a4192642f0b136d5159a501700"
        )
        assert transaction.parent_span_id != "1234567890abcdef"
        assert not transaction.parent_sampled


def test_continue_trace_forces_new_traces_when_no_propagation(sentry_init):
    """This is to make sure we don't have a long running trace because of TWP logic for the no propagation case."""

    sentry_init(traces_sample_rate=1.0)

    tx1 = continue_trace({}, name="tx1")
    tx2 = continue_trace({}, name="tx2")

    assert tx1.trace_id != tx2.trace_id
