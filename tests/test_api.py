from sentry_sdk import (
    configure_scope,
    continue_trace,
    get_baggage,
    get_current_span,
    get_traceparent,
    start_transaction,
)
from sentry_sdk.hub import Hub

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
