import pytest

from sentry_sdk import start_span, start_transaction
from sentry_sdk.tracing import Transaction


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
