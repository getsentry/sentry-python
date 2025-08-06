import pytest
from unittest.mock import MagicMock, ANY

import sentry_sdk
from sentry_sdk import start_span, get_current_scope
from sentry_sdk.consts import MATCH_ALL
from sentry_sdk.tracing_utils import should_propagate_trace
from sentry_sdk.utils import Dsn
from tests.conftest import ApproxDict


def test_span_trimming(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, _experiments={"max_spans": 3})
    events = capture_events()

    with start_span(name="hi"):
        for i in range(10):
            with start_span(op=f"foo{i}"):
                pass

    (event,) = events

    assert len(event["spans"]) == 3

    span1, span2, span3 = event["spans"]
    assert span1["op"] == "foo0"
    assert span2["op"] == "foo1"
    assert span3["op"] == "foo2"

    assert event["_meta"]["spans"][""]["len"] == 10
    assert "_dropped_spans" not in event


def test_span_data_scrubbing_and_trimming(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, _experiments={"max_spans": 3})
    events = capture_events()

    with start_span(name="hi"):
        with start_span(op="foo", name="bar") as span:
            span.set_attribute("password", "secret")
            span.set_attribute("datafoo", "databar")

        for i in range(10):
            with start_span(op=f"foo{i}"):
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
    with start_span():
        pass
    assert len(events) == 1
    assert events[0]["transaction"] == "<unlabeled span>"

    # the name can be set once the transaction's already started
    with start_span() as span:
        span.name = "name-known-after-transaction-started"
    assert len(events) == 2
    assert events[1]["transaction"] == "name-known-after-transaction-started"

    # passing in a name works, too
    with start_span(name="a"):
        pass
    assert len(events) == 3
    assert events[2]["transaction"] == "a"


def test_root_span_data(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(name="test-root-span"):
        root_span = sentry_sdk.get_current_span()
        root_span.set_attribute("foo", "bar")
        with start_span(op="test-span") as span:
            span.set_attribute("spanfoo", "spanbar")

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


def test_finds_spans_on_scope(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    with start_span(name="dogpark") as root_span:
        assert get_current_scope().span == root_span

        with start_span(name="child") as child_span:
            assert get_current_scope().span == child_span
            assert child_span.root_span == root_span


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


def test_span_set_data_update_data(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    with sentry_sdk.start_span(name="test-root-span"):
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
        "thread.id": ANY,
        "thread.name": ANY,
    }


def test_update_current_span(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    with sentry_sdk.start_span(name="test-root-span"):
        with start_span(op="test-span-op", name="test-span-name"):
            sentry_sdk.update_current_span(
                op="updated-span-op",
                name="updated-span-name",
                attributes={
                    "key0": "value0",
                    "key1": "value1",
                },
            )

            sentry_sdk.update_current_span(
                op="updated-span-op-2",
            )

            sentry_sdk.update_current_span(
                name="updated-span-name-3",
            )

            sentry_sdk.update_current_span(
                attributes={
                    "key1": "updated-value-4",
                    "key2": "value2",
                },
            )

    (event,) = events
    span = event["spans"][0]

    assert span["op"] == "updated-span-op-2"
    assert span["description"] == "updated-span-name-3"
    assert span["data"] == {
        "key0": "value0",
        "key1": "updated-value-4",
        "key2": "value2",
        "thread.id": ANY,
        "thread.name": ANY,
    }
