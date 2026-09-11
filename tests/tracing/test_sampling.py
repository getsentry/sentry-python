import random
from collections import Counter
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk import capture_exception
from sentry_sdk.utils import logger


def test_sampling_decided_only_for_segments(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=0.5,
        trace_lifecycle="stream",
    )

    with sentry_sdk.traces.start_span(name="hi") as segment:
        assert segment.sampled is not None

        with sentry_sdk.traces.start_span(name="hey") as span:
            assert span.sampled == segment.sampled


def test_no_double_sampling(sentry_init, capture_items):
    # Segments should not be subject to the global/error sample rate.
    # Only the traces_sample_rate should apply.
    sentry_init(
        traces_sample_rate=1.0,
        sample_rate=0.0,
        trace_lifecycle="stream",
    )
    items = capture_items()

    with sentry_sdk.traces.start_span(name="/"):
        pass

    sentry_sdk.flush()

    assert len(items) == 1


@pytest.mark.parametrize("sampling_decision", [True, False])
def test_get_span_from_scope_regardless_of_sampling_decision(
    sentry_init, sampling_decision
):
    sentry_init(traces_sample_rate=1.0, trace_lifecycle="stream")

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": f"0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-{int(sampling_decision)}"
        }
    )

    with sentry_sdk.traces.start_span(name="/"):
        with sentry_sdk.traces.start_span(name="child-span"):
            with sentry_sdk.traces.start_span(name="child-child-span"):
                scope = sentry_sdk.get_current_scope()
                if sampling_decision is True:
                    assert scope.streamed_span.name == "child-child-span"
                else:
                    # noop spans are not set on the scope unless they're segments
                    assert scope.streamed_span.name == "/"
                assert scope.transaction.name == "/"


@pytest.mark.parametrize(
    "traces_sample_rate,expected_decision",
    [(0.0, False), (0.25, False), (0.75, True), (1.00, True)],
)
def test_uses_traces_sample_rate_correctly(
    sentry_init,
    traces_sample_rate,
    expected_decision,
):
    sentry_init(
        traces_sample_rate=traces_sample_rate,
        trace_lifecycle="stream",
    )

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": "0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331",
            "baggage": "sentry-sample_rand=0.500000",
        }
    )

    with sentry_sdk.traces.start_span(name="dogpark") as span:
        assert span.sampled is expected_decision


@pytest.mark.parametrize(
    "traces_sampler_return_value,expected_decision",
    [(0.0, False), (0.25, False), (0.75, True), (1.00, True)],
)
def test_uses_traces_sampler_return_value_correctly(
    sentry_init,
    traces_sampler_return_value,
    expected_decision,
):
    sentry_init(
        traces_sampler=mock.Mock(return_value=traces_sampler_return_value),
        trace_lifecycle="stream",
    )

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": "0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331",
            "baggage": "sentry-sample_rand=0.500000",
        }
    )

    with sentry_sdk.traces.start_span(name="dogpark") as span:
        assert span.sampled is expected_decision


@pytest.mark.parametrize("traces_sampler_return_value", [True, False])
def test_tolerates_traces_sampler_returning_a_boolean(
    sentry_init, traces_sampler_return_value
):
    sentry_init(
        traces_sampler=mock.Mock(return_value=traces_sampler_return_value),
        trace_lifecycle="stream",
    )

    with sentry_sdk.traces.start_span(name="dogpark") as span:
        assert span.sampled is traces_sampler_return_value


@pytest.mark.parametrize("parent_sampling_decision", [True, False])
def test_traces_sampler_raising_falls_back_to_parent_sampling_decision(
    sentry_init, parent_sampling_decision
):
    # set traces_sample_rate to produce the opposite of the parent decision,
    # to prove the parent's decision takes precedence in the fallback
    sentry_init(
        traces_sampler=mock.Mock(side_effect=ValueError("boom")),
        traces_sample_rate=0.0 if parent_sampling_decision else 1.0,
        trace_lifecycle="stream",
    )

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": f"0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-{int(parent_sampling_decision)}"
        }
    )

    with sentry_sdk.traces.start_span(name="dogpark") as span:
        assert span.sampled is parent_sampling_decision


@pytest.mark.parametrize(
    "traces_sample_rate,expected_decision",
    [(0.0, False), (0.25, False), (0.75, True), (1.00, True)],
)
def test_traces_sampler_raising_falls_back_to_traces_sample_rate(
    sentry_init,
    traces_sample_rate,
    expected_decision,
):
    sentry_init(
        traces_sampler=mock.Mock(side_effect=ValueError("boom")),
        traces_sample_rate=traces_sample_rate,
        trace_lifecycle="stream",
    )

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": "0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331",
            "baggage": "sentry-sample_rand=0.500000",
        }
    )

    with sentry_sdk.traces.start_span(name="dogpark") as span:
        assert span.sampled is expected_decision


@pytest.mark.parametrize(
    "traces_sample_rate,expected_decision",
    [(0.0, False), (0.25, False), (0.75, True), (1.00, True)],
)
def test_traces_sampler_raising_no_incoming_trace_falls_back_to_traces_sample_rate(
    sentry_init,
    traces_sample_rate,
    expected_decision,
    monkeypatch,
):
    sentry_init(
        traces_sampler=mock.Mock(side_effect=ValueError("boom")),
        traces_sample_rate=traces_sample_rate,
        trace_lifecycle="stream",
    )

    # no continue_trace, so no propagated sample_rand; make it deterministic
    monkeypatch.setattr(
        "sentry_sdk.tracing_utils._generate_sample_rand", lambda *a, **kw: 0.5
    )

    with sentry_sdk.traces.start_span(name="dogpark") as span:
        assert span.sampled is expected_decision


def test_traces_sampler_raising_no_incoming_trace_and_no_traces_sample_rate(
    sentry_init,
):
    sentry_init(
        traces_sampler=mock.Mock(side_effect=ValueError("boom")),
        trace_lifecycle="stream",
    )

    with sentry_sdk.traces.start_span(name="dogpark") as span:
        assert span.sampled is False


@pytest.mark.parametrize("sampling_decision", [True, False])
def test_only_captures_segment_when_sampled_is_true(
    sentry_init, sampling_decision, capture_items
):
    sentry_init(
        traces_sampler=mock.Mock(
            return_value=sampling_decision,
        ),
        trace_lifecycle="stream",
    )
    items = capture_items()

    span = sentry_sdk.traces.start_span(name="dogpark")
    span.end()

    sentry_sdk.flush()

    assert len(items) == (1 if sampling_decision else 0)


@pytest.mark.parametrize(
    "traces_sample_rate,traces_sampler_return_value", [(0, True), (1, False)]
)
def test_prefers_traces_sampler_to_traces_sample_rate(
    sentry_init,
    traces_sample_rate,
    traces_sampler_return_value,
):
    # make traces_sample_rate imply the opposite of traces_sampler, to prove
    # that traces_sampler takes precedence
    traces_sampler = mock.Mock(return_value=traces_sampler_return_value)
    sentry_init(
        traces_sample_rate=traces_sample_rate,
        traces_sampler=traces_sampler,
        trace_lifecycle="stream",
    )

    span = sentry_sdk.traces.start_span(name="dogpark")
    assert traces_sampler.called is True
    assert span.sampled is traces_sampler_return_value


@pytest.mark.parametrize("parent_sampling_decision", ["1", "0"])
def test_ignores_inherited_sample_decision_when_traces_sampler_defined(
    sentry_init, parent_sampling_decision
):
    # make traces_sampler pick the opposite of the inherited decision, to prove
    # that traces_sampler takes precedence
    traces_sampler = mock.Mock(return_value=not bool(int(parent_sampling_decision)))
    sentry_init(
        traces_sampler=traces_sampler,
        trace_lifecycle="stream",
    )

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": f"0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-{parent_sampling_decision}"
        }
    )
    span = sentry_sdk.traces.start_span(name="dogpark")
    assert span.sampled is not bool(int(parent_sampling_decision))


@pytest.mark.parametrize("parent_sampling_decision", ["1", "0"])
def test_inherits_parent_sampling_decision_when_traces_sampler_undefined(
    sentry_init, parent_sampling_decision
):
    sentry_init(
        traces_sample_rate=0.5,
        trace_lifecycle="stream",
    )

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": f"0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-{parent_sampling_decision}"
        }
    )

    # make sure the parent sampling decision is the opposite of what
    # traces_sample_rate would produce, to prove the inheritance takes
    # precedence
    mock_random_value = 0.25 if parent_sampling_decision == "0" else 0.75
    with mock.patch.object(random, "random", return_value=mock_random_value):
        span = sentry_sdk.traces.start_span(name="dogpark")
        assert span.sampled is bool(int(parent_sampling_decision))


def test_custom_sampling_context(sentry_init):
    class MyClass: ...

    my_class = MyClass()

    def traces_sampler(sampling_context):
        assert "class" in sampling_context
        assert "string" in sampling_context
        assert sampling_context["class"] == my_class
        assert sampling_context["string"] == "my string"
        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        trace_lifecycle="stream",
    )

    sentry_sdk.get_current_scope().set_custom_sampling_context(
        {
            "class": my_class,
            "string": "my string",
        }
    )

    with sentry_sdk.traces.start_span(name="span"):
        ...


def test_custom_sampling_context_update_to_context_value_persists(sentry_init):
    def traces_sampler(sampling_context):
        if sampling_context["transaction_context"]["data"]["first"] is True:
            assert sampling_context["custom_value"] == 1
        else:
            assert sampling_context["custom_value"] == 2
        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        trace_lifecycle="stream",
    )

    sentry_sdk.traces.new_trace()

    sentry_sdk.get_current_scope().set_custom_sampling_context({"custom_value": 1})

    with sentry_sdk.traces.start_span(name="span", attributes={"first": True}):
        ...

    sentry_sdk.traces.new_trace()

    sentry_sdk.get_current_scope().set_custom_sampling_context({"custom_value": 2})

    with sentry_sdk.traces.start_span(name="span", attributes={"first": False}):
        ...


def test_sample_rate_affects_errors(sentry_init, capture_events):
    sentry_init(sample_rate=0)
    events = capture_events()

    try:
        1 / 0
    except Exception:
        capture_exception()

    assert len(events) == 0


@pytest.mark.parametrize(
    "traces_sampler_return_value",
    [
        "dogs are great",  # wrong type
        None,  # wrong type
        float("NaN"),  # wrong type (edge: float, but not a valid rate)
        -1.121,  # wrong value
        1.231,  # wrong value
    ],
)
def test_warns_and_sets_sampled_to_false_on_invalid_traces_sampler_return_value(
    sentry_init,
    traces_sampler_return_value,
    StringContaining,  # noqa: N803
):
    sentry_init(
        traces_sampler=mock.Mock(return_value=traces_sampler_return_value),
        trace_lifecycle="stream",
    )

    with mock.patch.object(logger, "warning", mock.Mock()):
        span = sentry_sdk.traces.start_span(name="dogpark")
        logger.warning.assert_any_call(StringContaining("Given sample rate is invalid"))
        assert span.sampled is False


@pytest.mark.parametrize(
    "traces_sample_rate,sampled_output,expected_record_lost_event_calls",
    [
        (None, None, []),
        (
            0.0,
            False,
            [("sample_rate", "span", None, 1)],
        ),
        (1.0, True, []),
    ],
)
def test_records_lost_event_only_if_traces_sample_rate_enabled(
    sentry_init,
    capture_record_lost_event_calls,
    traces_sample_rate,
    sampled_output,
    expected_record_lost_event_calls,
):
    sentry_init(traces_sample_rate=traces_sample_rate, trace_lifecycle="stream")
    record_lost_event_calls = capture_record_lost_event_calls()

    span = sentry_sdk.traces.start_span(name="dogpark")
    assert span.sampled is sampled_output
    span.end()

    # Use Counter because order of calls does not matter
    assert Counter(record_lost_event_calls) == Counter(expected_record_lost_event_calls)


@pytest.mark.parametrize(
    "traces_sampler,sampled_output,expected_record_lost_event_calls",
    [
        (None, None, []),
        (
            lambda _x: 0.0,
            False,
            [("sample_rate", "span", None, 1)],
        ),
        (lambda _x: 1.0, True, []),
    ],
)
def test_records_lost_event_only_if_traces_sampler_enabled(
    sentry_init,
    capture_record_lost_event_calls,
    traces_sampler,
    sampled_output,
    expected_record_lost_event_calls,
):
    sentry_init(traces_sampler=traces_sampler, trace_lifecycle="stream")
    record_lost_event_calls = capture_record_lost_event_calls()

    segment = sentry_sdk.traces.start_span(name="dogpark")
    assert segment.sampled is sampled_output
    segment.end()

    # Use Counter because order of calls does not matter
    assert Counter(record_lost_event_calls) == Counter(expected_record_lost_event_calls)


def test_unsampled_spans_produce_client_report_if_traces_sample_rate_defined(
    sentry_init, capture_items, capture_record_lost_event_calls
):
    sentry_init(
        traces_sample_rate=0.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")
    record_lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="segment"):
        with sentry_sdk.traces.start_span(name="child1"):
            pass
        with sentry_sdk.traces.start_span(name="child2"):
            pass

    sentry_sdk.get_client().flush()

    spans = [item.payload for item in items]
    assert not spans

    assert record_lost_event_calls == [
        ("sample_rate", "span", None, 1),
        ("sample_rate", "span", None, 1),
        ("sample_rate", "span", None, 1),
    ]


def test_unsampled_spans_produce_client_report_if_traces_sampler_defined(
    sentry_init, capture_items, capture_record_lost_event_calls
):
    sentry_init(
        traces_sampler=lambda _: 0.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")
    record_lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="segment"):
        with sentry_sdk.traces.start_span(name="child1"):
            pass
        with sentry_sdk.traces.start_span(name="child2"):
            pass

    sentry_sdk.get_client().flush()

    spans = [item.payload for item in items]
    assert not spans

    assert record_lost_event_calls == [
        ("sample_rate", "span", None, 1),
        ("sample_rate", "span", None, 1),
        ("sample_rate", "span", None, 1),
    ]


def test_no_client_reports_if_tracing_is_off(
    sentry_init, capture_items, capture_record_lost_event_calls
):
    sentry_init(
        traces_sample_rate=None,
        trace_lifecycle="stream",
    )

    items = capture_items("span")
    record_lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="segment"):
        with sentry_sdk.traces.start_span(name="child1"):
            pass
        with sentry_sdk.traces.start_span(name="child2"):
            pass

    sentry_sdk.get_client().flush()

    spans = [item.payload for item in items]
    assert not spans
    assert not record_lost_event_calls
