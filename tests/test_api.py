from datetime import datetime

from sentry_sdk import (
    configure_scope,
    continue_trace,
    get_baggage,
    get_current_span,
    get_traceparent,
    start_transaction,
    start_span,
)
from sentry_sdk.hub import Hub
from sentry_sdk.tracing import Span, Transaction

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def test_get_current_span():
    fake_hub = mock.MagicMock()
    fake_hub.scope = mock.MagicMock()

    fake_hub.scope.span = mock.MagicMock()
    assert get_current_span(fake_hub) == fake_hub.scope.span

    fake_hub.scope.span = None
    assert get_current_span(fake_hub) is None


def test_get_current_span_default_hub(sentry_init):
    sentry_init()

    assert get_current_span() is None

    with configure_scope() as scope:
        fake_span = mock.MagicMock()
        scope.span = fake_span

        assert get_current_span() == fake_span


def test_get_current_span_default_hub_with_transaction(sentry_init):
    sentry_init()

    assert get_current_span() is None

    with start_transaction() as new_transaction:
        assert get_current_span() == new_transaction


def test_traceparent_with_tracing_enabled(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    with start_transaction() as transaction:
        expected_traceparent = "%s-%s-1" % (
            transaction.trace_id,
            transaction.span_id,
        )
        assert get_traceparent() == expected_traceparent


def test_traceparent_with_tracing_disabled(sentry_init):
    sentry_init()

    propagation_context = Hub.current.scope._propagation_context
    expected_traceparent = "%s-%s" % (
        propagation_context["trace_id"],
        propagation_context["span_id"],
    )
    assert get_traceparent() == expected_traceparent


def test_baggage_with_tracing_disabled(sentry_init):
    sentry_init(release="1.0.0", environment="dev")
    propagation_context = Hub.current.scope._propagation_context
    expected_baggage = (
        "sentry-trace_id={},sentry-environment=dev,sentry-release=1.0.0".format(
            propagation_context["trace_id"]
        )
    )
    # order not guaranteed in older python versions
    assert sorted(get_baggage().split(",")) == sorted(expected_baggage.split(","))


def test_baggage_with_tracing_enabled(sentry_init):
    sentry_init(traces_sample_rate=1.0, release="1.0.0", environment="dev")
    with start_transaction() as transaction:
        expected_baggage = "sentry-trace_id={},sentry-environment=dev,sentry-release=1.0.0,sentry-sample_rate=1.0,sentry-sampled={}".format(
            transaction.trace_id, "true" if transaction.sampled else "false"
        )
        # order not guaranteed in older python versions
        assert sorted(get_baggage().split(",")) == sorted(expected_baggage.split(","))


def test_continue_trace(sentry_init):
    sentry_init()

    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    transaction = continue_trace(
        {
            "sentry-trace": "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled),
            "baggage": "sentry-trace_id=566e3688a61d4bc888951642d6f14a19",
        },
        name="some name",
    )
    with start_transaction(transaction):
        assert transaction.name == "some name"

        propagation_context = Hub.current.scope._propagation_context
        assert propagation_context["trace_id"] == transaction.trace_id == trace_id
        assert propagation_context["parent_span_id"] == parent_span_id
        assert propagation_context["parent_sampled"] == parent_sampled
        assert propagation_context["dynamic_sampling_context"] == {
            "trace_id": "566e3688a61d4bc888951642d6f14a19"
        }


def test_start_span_creates_new_transaction(sentry_init):
    sentry_init(traces_sample_rate=1.0)
    hub = Hub.current
    now = datetime.utcnow()

    start_span_parameters = {
        "op": "test_op",
        "description": "test_description",
        "span_id": "1111111111111111",
        "parent_span_id": "2222222222222222",
        "trace_id": "33333333333333333333333333333333",
        "same_process_as_parent": True,
        "sampled": False,
        "hub": hub,
        "status": "dummy-status",
        "containing_transaction": None,  # todo add some transaction
        "start_timestamp": now,
    }

    expected_result_json = {
        "op": "test_op",
        "name": "test_description",
        "description": None,  # The transaction having a None description in the JSON is existing behaviour
        "span_id": "1111111111111111",
        "parent_span_id": "2222222222222222",
        "trace_id": "33333333333333333333333333333333",
        "same_process_as_parent": True,
        "start_timestamp": now,
        "timestamp": None,  # transaction not finished yet, thus None
        "tags": {
            "status": "dummy-status",
            "some_tag_key": "some_tag_value",
        },
        "data": {
            "some_key": "some_value",
        },
        "source": "custom",
        "sampled": False,
    }

    with start_span(**start_span_parameters) as span:
        span.set_data("some_key", "some_value")
        span.set_tag("some_tag_key", "some_tag_value")

        assert type(span) == Transaction
        assert span.containing_transaction == span
        assert span.to_json() == expected_result_json


def test_start_span_uses_existing_transaction(sentry_init):
    sentry_init(traces_sample_rate=1.0)
    hub = Hub.current
    now = datetime.utcnow()

    start_span_parameters = {
        "op": "test_op",
        "description": "test_description",
        "span_id": "1111111111111111",
        "parent_span_id": "2222222222222222",
        "trace_id": "33333333333333333333333333333333",
        "same_process_as_parent": True,
        "sampled": False,
        "hub": hub,
        "status": "dummy-status",
        "containing_transaction": None,  # todo add some transaction
        "start_timestamp": now,
    }

    expected_result_json = {
        "op": "test_op",
        # there is no "name" in a span
        "description": "test_description",
        "span_id": "1111111111111111",
        "parent_span_id": None,  # will be set from transaciton, see below
        "trace_id": None,  # will be set from transaciton, see below
        "same_process_as_parent": True,
        "start_timestamp": now,
        "timestamp": None,  # Span not finished yet, thus None
        "tags": {
            "status": "dummy-status",
            "some_tag_key": "some_tag_value",
        },
        "data": {
            "some_key": "some_value",
        },
        # there is no "source" in a span
        # there is no "sampled" in a span
    }

    with start_transaction(name="some-existing-transaction") as transaction:
        with start_span(**start_span_parameters) as span:
            span.set_data("some_key", "some_value")
            span.set_tag("some_tag_key", "some_tag_value")

            expected_result_json["trace_id"] = transaction.trace_id
            expected_result_json["parent_span_id"] = transaction.span_id

            assert type(span) == Span
            assert span.containing_transaction == transaction
            assert span.to_json() == expected_result_json
