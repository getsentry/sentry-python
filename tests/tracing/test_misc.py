import pytest
import gc
import uuid
import os
from unittest import mock
from unittest.mock import MagicMock

import sentry_sdk
from sentry_sdk import start_span, start_transaction, set_measurement
from sentry_sdk.consts import MATCH_ALL
from sentry_sdk.tracing import Span, Transaction
from sentry_sdk.tracing_utils import should_propagate_trace
from sentry_sdk.utils import Dsn
from tests.conftest import ApproxDict


def test_span_trimming(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, _experiments={"max_spans": 3})
    events = capture_events()

    with start_transaction(name="hi"):
        for i in range(10):
            with start_span(op="foo{}".format(i)):
                pass

    (event,) = events

    assert len(event["spans"]) == 3

    span1, span2, span3 = event["spans"]
    assert span1["op"] == "foo0"
    assert span2["op"] == "foo1"
    assert span3["op"] == "foo2"

    assert event["_meta"]["spans"][""]["len"] == 10
    assert "_dropped_spans" not in event
    assert "dropped_spans" not in event


def test_span_data_scrubbing_and_trimming(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, _experiments={"max_spans": 3})
    events = capture_events()

    with start_transaction(name="hi"):
        with start_span(op="foo", name="bar") as span:
            span.set_data("password", "secret")
            span.set_data("datafoo", "databar")

        for i in range(10):
            with start_span(op="foo{}".format(i)):
                pass

    (event,) = events
    assert event["spans"][0]["data"] == ApproxDict(
        {"password": "[Filtered]", "datafoo": "databar"}
    )
    assert event["_meta"]["spans"] == {
        "0": {"data": {"password": {"": {"rem": [["!config", "s"]]}}}},
        "": {"len": 11},
    }


def test_transaction_naming(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    # default name in event if no name is passed
    with start_transaction() as transaction:
        pass
    assert len(events) == 1
    assert events[0]["transaction"] == "<unlabeled transaction>"

    # the name can be set once the transaction's already started
    with start_transaction() as transaction:
        transaction.name = "name-known-after-transaction-started"
    assert len(events) == 2
    assert events[1]["transaction"] == "name-known-after-transaction-started"

    # passing in a name works, too
    with start_transaction(name="a"):
        pass
    assert len(events) == 3
    assert events[2]["transaction"] == "a"


def test_transaction_data(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction(name="test-transaction"):
        span_or_tx = sentry_sdk.get_current_span()
        span_or_tx.set_data("foo", "bar")
        with start_span(op="test-span") as span:
            span.set_data("spanfoo", "spanbar")

    assert len(events) == 1

    transaction = events[0]
    transaction_data = transaction["contexts"]["trace"]["data"]

    assert "data" not in transaction.keys()
    assert transaction_data.items() >= {"foo": "bar"}.items()

    assert len(transaction["spans"]) == 1

    span = transaction["spans"][0]
    span_data = span["data"]

    assert "contexts" not in span.keys()
    assert span_data.items() >= {"spanfoo": "spanbar"}.items()


def test_start_transaction(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    # you can have it start a transaction for you
    result1 = start_transaction(
        name="/interactions/other-dogs/new-dog", op="greeting.sniff"
    )
    assert isinstance(result1, Transaction)
    assert result1.name == "/interactions/other-dogs/new-dog"
    assert result1.op == "greeting.sniff"

    # or you can pass it an already-created transaction
    preexisting_transaction = Transaction(
        name="/interactions/other-dogs/new-dog", op="greeting.sniff"
    )
    result2 = start_transaction(preexisting_transaction)
    assert result2 is preexisting_transaction


def test_finds_transaction_on_scope(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    transaction = start_transaction(name="dogpark")

    scope = sentry_sdk.get_current_scope()

    # See note in Scope class re: getters and setters of the `transaction`
    # property. For the moment, assigning to scope.transaction merely sets the
    # transaction name, rather than putting the transaction on the scope, so we
    # have to assign to _span directly.
    scope._span = transaction

    # Reading scope.property, however, does what you'd expect, and returns the
    # transaction on the scope.
    assert scope.transaction is not None
    assert isinstance(scope.transaction, Transaction)
    assert scope.transaction.name == "dogpark"

    # If the transaction is also set as the span on the scope, it can be found
    # by accessing _span, too.
    assert scope._span is not None
    assert isinstance(scope._span, Transaction)
    assert scope._span.name == "dogpark"


def test_finds_transaction_when_descendent_span_is_on_scope(
    sentry_init,
):
    sentry_init(traces_sample_rate=1.0)

    transaction = start_transaction(name="dogpark")
    child_span = transaction.start_child(op="sniffing")

    scope = sentry_sdk.get_current_scope()
    scope._span = child_span

    # this is the same whether it's the transaction itself or one of its
    # decedents directly attached to the scope
    assert scope.transaction is not None
    assert isinstance(scope.transaction, Transaction)
    assert scope.transaction.name == "dogpark"

    # here we see that it is in fact the span on the scope, rather than the
    # transaction itself
    assert scope._span is not None
    assert isinstance(scope._span, Span)
    assert scope._span.op == "sniffing"


def test_finds_orphan_span_on_scope(sentry_init):
    # this is deprecated behavior which may be removed at some point (along with
    # the start_span function)
    sentry_init(traces_sample_rate=1.0)

    span = start_span(op="sniffing")

    scope = sentry_sdk.get_current_scope()
    scope._span = span

    assert scope._span is not None
    assert isinstance(scope._span, Span)
    assert scope._span.op == "sniffing"


def test_finds_non_orphan_span_on_scope(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    transaction = start_transaction(name="dogpark")
    child_span = transaction.start_child(op="sniffing")

    scope = sentry_sdk.get_current_scope()
    scope._span = child_span

    assert scope._span is not None
    assert isinstance(scope._span, Span)
    assert scope._span.op == "sniffing"


def test_circular_references(monkeypatch, sentry_init, request):
    # TODO: We discovered while writing this test about transaction/span
    # reference cycles that there's actually also a circular reference in
    # `serializer.py`, between the functions `_serialize_node` and
    # `_serialize_node_impl`, both of which are defined inside of the main
    # `serialize` function, and each of which calls the other one. For now, in
    # order to avoid having those ref cycles give us a false positive here, we
    # can mock out `serialize`. In the long run, though, we should probably fix
    # that. (Whenever we do work on fixing it, it may be useful to add
    #
    #     gc.set_debug(gc.DEBUG_LEAK)
    #     request.addfinalizer(lambda: gc.set_debug(~gc.DEBUG_LEAK))
    #
    # immediately after the initial collection below, so we can see what new
    # objects the garbage collector has to clean up once `transaction.finish` is
    # called and the serializer runs.)
    monkeypatch.setattr(
        sentry_sdk.client,
        "serialize",
        mock.Mock(
            return_value=None,
        ),
    )

    # In certain versions of python, in some environments (specifically, python
    # 3.4 when run in GH Actions), we run into a `ctypes` bug which creates
    # circular references when `uuid4()` is called, as happens when we're
    # generating event ids. Mocking it with an implementation which doesn't use
    # the `ctypes` function lets us avoid having false positives when garbage
    # collecting. See https://bugs.python.org/issue20519.
    monkeypatch.setattr(
        uuid,
        "uuid4",
        mock.Mock(
            return_value=uuid.UUID(bytes=os.urandom(16)),
        ),
    )

    gc.disable()
    request.addfinalizer(gc.enable)

    sentry_init(traces_sample_rate=1.0)

    # Make sure that we're starting with a clean slate before we start creating
    # transaction/span reference cycles
    gc.collect()

    dogpark_transaction = start_transaction(name="dogpark")
    sniffing_span = dogpark_transaction.start_child(op="sniffing")
    wagging_span = dogpark_transaction.start_child(op="wagging")

    # At some point, you have to stop sniffing - there are balls to chase! - so finish
    # this span while the dogpark transaction is still open
    sniffing_span.finish()

    # The wagging, however, continues long past the dogpark, so that span will
    # NOT finish before the transaction ends. (Doing it in this order proves
    # that both finished and unfinished spans get their cycles broken.)
    dogpark_transaction.finish()

    # Eventually you gotta sleep...
    wagging_span.finish()

    # assuming there are no cycles by this point, these should all be able to go
    # out of scope and get their memory deallocated without the garbage
    # collector having anything to do
    del sniffing_span
    del wagging_span
    del dogpark_transaction

    assert gc.collect() == 0


def test_set_meaurement(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    transaction = start_transaction(name="measuring stuff")

    with pytest.raises(TypeError):
        transaction.set_measurement()

    with pytest.raises(TypeError):
        transaction.set_measurement("metric.foo")

    transaction.set_measurement("metric.foo", 123)
    transaction.set_measurement("metric.bar", 456, unit="second")
    transaction.set_measurement("metric.baz", 420.69, unit="custom")
    transaction.set_measurement("metric.foobar", 12, unit="percent")
    transaction.set_measurement("metric.foobar", 17.99, unit="percent")

    transaction.finish()

    (event,) = events
    assert event["measurements"]["metric.foo"] == {"value": 123, "unit": ""}
    assert event["measurements"]["metric.bar"] == {"value": 456, "unit": "second"}
    assert event["measurements"]["metric.baz"] == {"value": 420.69, "unit": "custom"}
    assert event["measurements"]["metric.foobar"] == {"value": 17.99, "unit": "percent"}


def test_set_meaurement_public_api(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    with start_transaction(name="measuring stuff"):
        set_measurement("metric.foo", 123)
        set_measurement("metric.bar", 456, unit="second")

    (event,) = events
    assert event["measurements"]["metric.foo"] == {"value": 123, "unit": ""}
    assert event["measurements"]["metric.bar"] == {"value": 456, "unit": "second"}


def test_set_measurement_deprecated(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    with start_transaction(name="measuring stuff") as trx:
        with pytest.warns(DeprecationWarning):
            set_measurement("metric.foo", 123)

        with pytest.warns(DeprecationWarning):
            trx.set_measurement("metric.bar", 456)

        with start_span(op="measuring span") as span:
            with pytest.warns(DeprecationWarning):
                span.set_measurement("metric.baz", 420.69, unit="custom")


def test_set_meaurement_compared_to_set_data(sentry_init, capture_events):
    """
    This is just a test to see the difference
    between measurements and data in the resulting event payload.
    """
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    with start_transaction(name="measuring stuff") as transaction:
        transaction.set_measurement("metric.foo", 123)
        transaction.set_data("metric.bar", 456)

        with start_span(op="measuring span") as span:
            span.set_measurement("metric.baz", 420.69, unit="custom")
            span.set_data("metric.qux", 789)

    (event,) = events
    assert event["measurements"]["metric.foo"] == {"value": 123, "unit": ""}
    assert event["contexts"]["trace"]["data"]["metric.bar"] == 456
    assert event["spans"][0]["measurements"]["metric.baz"] == {
        "value": 420.69,
        "unit": "custom",
    }
    assert event["spans"][0]["data"]["metric.qux"] == 789


@pytest.mark.parametrize(
    "trace_propagation_targets,url,expected_propagation_decision",
    [
        (None, "http://example.com", False),
        ([], "http://example.com", False),
        ([MATCH_ALL], "http://example.com", True),
        (["localhost"], "localhost:8443/api/users", True),
        (["localhost"], "http://localhost:8443/api/users", True),
        (["localhost"], "mylocalhost:8080/api/users", True),
        ([r"^/api"], "/api/envelopes", True),
        ([r"^/api"], "/backend/api/envelopes", False),
        ([r"myApi.com/v[2-4]"], "myApi.com/v2/projects", True),
        ([r"myApi.com/v[2-4]"], "myApi.com/v1/projects", False),
        ([r"https:\/\/.*"], "https://example.com", True),
        (
            [r"https://.*"],
            "https://example.com",
            True,
        ),  # to show escaping is not needed
        ([r"https://.*"], "http://example.com/insecure/", False),
    ],
)
def test_should_propagate_trace(
    trace_propagation_targets, url, expected_propagation_decision
):
    client = MagicMock()

    # This test assumes the urls are not Sentry URLs. Use test_should_propagate_trace_to_sentry for sentry URLs.
    client.is_sentry_url = lambda _: False

    client.options = {"trace_propagation_targets": trace_propagation_targets}
    client.transport = MagicMock()
    client.transport.parsed_dsn = Dsn("https://bla@xxx.sentry.io/12312012")

    assert should_propagate_trace(client, url) == expected_propagation_decision


@pytest.mark.parametrize(
    "dsn,url,expected_propagation_decision",
    [
        (
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            "http://example.com",
            True,
        ),
        (
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            False,
        ),
        (
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            "http://squirrelchasers.ingest.sentry.io/12312012",
            False,
        ),
        (
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            "http://ingest.sentry.io/12312012",
            True,
        ),
        (
            "https://abc@localsentry.example.com/12312012",
            "http://localsentry.example.com",
            False,
        ),
    ],
)
def test_should_propagate_trace_to_sentry(
    sentry_init, dsn, url, expected_propagation_decision
):
    sentry_init(
        dsn=dsn,
        traces_sample_rate=1.0,
    )

    client = sentry_sdk.get_client()
    client.transport.parsed_dsn = Dsn(dsn)

    assert should_propagate_trace(client, url) == expected_propagation_decision


def test_start_transaction_updates_scope_name_source(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    scope = sentry_sdk.get_current_scope()

    with start_transaction(name="foobar", source="route"):
        assert scope._transaction == "foobar"
        assert scope._transaction_info == {"source": "route"}


@pytest.mark.parametrize("sampled", (True, None))
def test_transaction_dropped_debug_not_started(sentry_init, sampled):
    sentry_init(enable_tracing=True)

    tx = Transaction(sampled=sampled)

    with mock.patch("sentry_sdk.tracing.logger") as mock_logger:
        with tx:
            pass

    mock_logger.debug.assert_any_call(
        "Discarding transaction because it was not started with sentry_sdk.start_transaction"
    )

    with pytest.raises(AssertionError):
        # We should NOT see the "sampled = False" message here
        mock_logger.debug.assert_any_call(
            "Discarding transaction because sampled = False"
        )


def test_transaction_dropeed_sampled_false(sentry_init):
    sentry_init(enable_tracing=True)

    tx = Transaction(sampled=False)

    with mock.patch("sentry_sdk.tracing.logger") as mock_logger:
        with sentry_sdk.start_transaction(tx):
            pass

    mock_logger.debug.assert_any_call("Discarding transaction because sampled = False")

    with pytest.raises(AssertionError):
        # We should not see the "not started" message here
        mock_logger.debug.assert_any_call(
            "Discarding transaction because it was not started with sentry_sdk.start_transaction"
        )


def test_transaction_not_started_warning(sentry_init):
    sentry_init(enable_tracing=True)

    tx = Transaction()

    with mock.patch("sentry_sdk.tracing.logger") as mock_logger:
        with tx:
            pass

    mock_logger.debug.assert_any_call(
        "Transaction was entered without being started with sentry_sdk.start_transaction."
        "The transaction will not be sent to Sentry. To fix, start the transaction by"
        "passing it to sentry_sdk.start_transaction."
    )


def test_span_set_data_update_data(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    with sentry_sdk.start_transaction(name="test-transaction"):
        with start_span(op="test-span") as span:
            span.set_data("key0", "value0")
            span.set_data("key1", "value1")

            span.update_data(
                {
                    "key1": "updated-value1",
                    "key2": "value2",
                    "key3": "value3",
                }
            )

    (event,) = events
    span = event["spans"][0]

    assert span["data"] == {
        "key0": "value0",
        "key1": "updated-value1",
        "key2": "value2",
        "key3": "value3",
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }
