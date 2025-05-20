import pytest

from unittest import mock

from sentry_sdk import (
    capture_exception,
    continue_trace,
    get_baggage,
    get_client,
    get_current_span,
    get_traceparent,
    is_initialized,
    start_span,
    set_tags,
    get_global_scope,
    get_current_scope,
    get_isolation_scope,
)

from sentry_sdk.client import Client, NonRecordingClient
from tests.conftest import SortedBaggage


@pytest.mark.forked
def test_get_current_span():
    fake_scope = mock.MagicMock()
    fake_scope.span = mock.MagicMock()
    assert get_current_span(fake_scope) == fake_scope.span

    fake_scope.span = None
    assert get_current_span(fake_scope) is None


@pytest.mark.forked
def test_get_current_span_current_scope(sentry_init):
    sentry_init()

    assert get_current_span() is None

    scope = get_current_scope()
    fake_span = mock.MagicMock()
    scope.span = fake_span

    assert get_current_span() == fake_span


@pytest.mark.forked
def test_get_current_span_current_scope_with_span(sentry_init):
    sentry_init()

    assert get_current_span() is None

    with start_span() as new_span:
        assert get_current_span() == new_span


@pytest.mark.forked
def test_traceparent_with_tracing_enabled(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    with start_span() as span:
        expected_traceparent = "%s-%s-1" % (
            span.trace_id,
            span.span_id,
        )
        assert get_traceparent() == expected_traceparent


@pytest.mark.forked
def test_traceparent_with_tracing_disabled(sentry_init):
    sentry_init()

    propagation_context = get_isolation_scope()._propagation_context
    expected_traceparent = "%s-%s" % (
        propagation_context.trace_id,
        propagation_context.span_id,
    )
    assert get_traceparent() == expected_traceparent


@pytest.mark.forked
def test_baggage_with_tracing_disabled(sentry_init):
    sentry_init(release="1.0.0", environment="dev")
    propagation_context = get_isolation_scope()._propagation_context
    expected_baggage = (
        "sentry-trace_id={},sentry-environment=dev,sentry-release=1.0.0".format(
            propagation_context.trace_id
        )
    )
    assert get_baggage() == SortedBaggage(expected_baggage)


@pytest.mark.forked
def test_baggage_with_tracing_enabled(sentry_init):
    sentry_init(traces_sample_rate=1.0, release="1.0.0", environment="dev")
    with mock.patch("sentry_sdk.tracing_utils.Random.uniform", return_value=0.111111):
        with start_span(name="foo") as span:
            expected_baggage = f"sentry-transaction=foo,sentry-trace_id={span.trace_id},sentry-sample_rand=0.111111,sentry-environment=dev,sentry-release=1.0.0,sentry-sample_rate=1.0,sentry-sampled=true"  # noqa: E231
            assert get_baggage() == SortedBaggage(expected_baggage)


@pytest.mark.forked
def test_continue_trace(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1

    with continue_trace(
        {
            "sentry-trace": "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled),
            "baggage": "sentry-trace_id=566e3688a61d4bc888951642d6f14a19,sentry-sample_rand=0.123456",  # noqa: E231
        },
    ):
        with start_span(name="some name") as span:
            assert span.name == "some name"
            propagation_context = get_isolation_scope()._propagation_context
            assert propagation_context.trace_id == span.trace_id == trace_id
            assert propagation_context.parent_span_id == parent_span_id
            assert propagation_context.parent_sampled == parent_sampled
            assert propagation_context.dynamic_sampling_context == {
                "trace_id": "566e3688a61d4bc888951642d6f14a19",
                "sample_rand": "0.123456",
            }


@pytest.mark.forked
def test_is_initialized():
    assert not is_initialized()

    scope = get_global_scope()
    scope.set_client(Client())
    assert is_initialized()


@pytest.mark.forked
def test_get_client():
    client = get_client()
    assert client is not None
    assert client.__class__ == NonRecordingClient
    assert not client.is_active()


def raise_and_capture():
    """Raise an exception and capture it.

    This is a utility function for test_set_tags.
    """
    try:
        1 / 0
    except ZeroDivisionError:
        capture_exception()


def test_set_tags(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    set_tags({"tag1": "value1", "tag2": "value2"})
    raise_and_capture()

    (*_, event) = events
    assert event["tags"] == {"tag1": "value1", "tag2": "value2"}, "Setting tags failed"

    set_tags({"tag2": "updated", "tag3": "new"})
    raise_and_capture()

    (*_, event) = events
    assert event["tags"] == {
        "tag1": "value1",
        "tag2": "updated",
        "tag3": "new",
    }, "Updating tags failed"

    set_tags({})
    raise_and_capture()

    (*_, event) = events
    assert event["tags"] == {
        "tag1": "value1",
        "tag2": "updated",
        "tag3": "new",
    }, "Updating tags with empty dict changed tags"
