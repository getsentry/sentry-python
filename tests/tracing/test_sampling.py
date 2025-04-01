import random
from collections import Counter
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk import start_span, capture_exception
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME, SENTRY_TRACE_HEADER_NAME
from sentry_sdk.utils import logger


def test_sampling_decided_only_for_root_spans(sentry_init):
    sentry_init(traces_sample_rate=0.5)

    with start_span(name="outer1") as root_span1:
        assert root_span1.sampled is not None

        with start_span(name="inner") as span:
            assert span.sampled == root_span1.sampled

    with start_span(name="outer2") as root_span2:
        assert root_span2.sampled is not None


@pytest.mark.parametrize("sampled", [True, False])
def test_nested_span_sampling_override(sentry_init, sampled):
    sentry_init(traces_sample_rate=1.0)

    with start_span(name="outer", sampled=sampled) as outer_span:
        assert outer_span.sampled is sampled
        with start_span(name="inner", sampled=(not sampled)) as inner_span:
            # won't work because the child span inherits the sampling decision
            # from the parent
            assert inner_span.sampled is sampled
        assert outer_span.sampled is sampled


def test_no_double_sampling(sentry_init, capture_events):
    # Transactions should not be subject to the global/error sample rate.
    # Only the traces_sample_rate should apply.
    sentry_init(traces_sample_rate=1.0, sample_rate=0.0)
    events = capture_events()

    with start_span(name="/"):
        pass

    assert len(events) == 1


@pytest.mark.parametrize(
    "traces_sample_rate,expected_decision",
    [(0.0, False), (0.25, False), (0.75, True), (1.00, True)],
)
def test_uses_traces_sample_rate_correctly(
    sentry_init,
    traces_sample_rate,
    expected_decision,
):
    sentry_init(traces_sample_rate=traces_sample_rate)

    with sentry_sdk.continue_trace(
        {
            BAGGAGE_HEADER_NAME: "sentry-sample_rand=0.500000,sentry-trace_id=397f36434d07b20135324b2e6ae70c77",
            SENTRY_TRACE_HEADER_NAME: "397f36434d07b20135324b2e6ae70c77-1234567890abcdef",
        }
    ):
        with start_span(name="dogpark") as root_span:
            assert root_span.sampled is expected_decision


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
        traces_sample_rate=1.0,
        traces_sampler=mock.Mock(return_value=traces_sampler_return_value),
    )

    with sentry_sdk.continue_trace(
        {
            BAGGAGE_HEADER_NAME: "sentry-sample_rand=0.500000,sentry-trace_id=397f36434d07b20135324b2e6ae70c77",
            SENTRY_TRACE_HEADER_NAME: "397f36434d07b20135324b2e6ae70c77-1234567890abcdef",
        }
    ):
        with start_span(name="dogpark") as root_span:
            assert root_span.sampled is expected_decision


@pytest.mark.parametrize("traces_sampler_return_value", [True, False])
def test_tolerates_traces_sampler_returning_a_boolean(
    sentry_init, traces_sampler_return_value
):
    sentry_init(
        traces_sample_rate=1.0,
        traces_sampler=mock.Mock(return_value=traces_sampler_return_value),
    )

    with start_span(name="dogpark") as span:
        assert span.sampled is traces_sampler_return_value


@pytest.mark.parametrize("sampling_decision", [True, False])
def test_only_captures_transaction_when_sampled_is_true(
    sentry_init, sampling_decision, capture_events
):
    sentry_init(
        traces_sample_rate=1.0,
        traces_sampler=mock.Mock(return_value=sampling_decision),
    )
    events = capture_events()

    with start_span(name="dogpark"):
        pass

    assert len(events) == (1 if sampling_decision else 0)


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
    )

    with start_span(name="dogpark") as span:
        assert traces_sampler.called is True
        assert span.sampled is traces_sampler_return_value


@pytest.mark.parametrize("parent_sampling_decision", [True, False])
def test_ignores_inherited_sample_decision_when_traces_sampler_defined(
    sentry_init, parent_sampling_decision
):
    # make traces_sampler pick the opposite of the inherited decision, to prove
    # that traces_sampler takes precedence
    traces_sampler = mock.Mock(return_value=not parent_sampling_decision)
    sentry_init(
        traces_sample_rate=1.0,
        traces_sampler=traces_sampler,
    )

    sentry_trace_header = (
        "12312012123120121231201212312012-1121201211212012-{sampled}".format(
            sampled=int(parent_sampling_decision)
        )
    )

    with sentry_sdk.continue_trace({"sentry-trace": sentry_trace_header}):
        with sentry_sdk.start_span(name="dogpark") as span:
            pass

    assert span.sampled is not parent_sampling_decision


@pytest.mark.parametrize("explicit_decision", [True, False])
def test_traces_sampler_doesnt_overwrite_explicitly_passed_sampling_decision(
    sentry_init, explicit_decision
):
    # make traces_sampler pick the opposite of the explicit decision, to prove
    # that the explicit decision takes precedence
    traces_sampler = mock.Mock(return_value=not explicit_decision)
    sentry_init(
        traces_sample_rate=1.0,
        traces_sampler=traces_sampler,
    )

    with start_span(name="dogpark", sampled=explicit_decision) as span:
        assert span.sampled is explicit_decision


@pytest.mark.parametrize("parent_sampling_decision", [True, False])
def test_inherits_parent_sampling_decision_when_traces_sampler_undefined(
    sentry_init, parent_sampling_decision
):
    # make sure the parent sampling decision is the opposite of what
    # traces_sample_rate would produce, to prove the inheritance takes
    # precedence
    sentry_init(traces_sample_rate=0.5)
    mock_random_value = 0.25 if parent_sampling_decision is False else 0.75

    sentry_trace_header = (
        "12312012123120121231201212312012-1121201211212012-{sampled}".format(
            sampled=int(parent_sampling_decision)
        )
    )
    with mock.patch.object(random, "random", return_value=mock_random_value):
        with sentry_sdk.continue_trace({"sentry-trace": sentry_trace_header}):
            with start_span(name="dogpark") as span:
                assert span.sampled is parent_sampling_decision


@pytest.mark.parametrize("parent_sampling_decision", [True, False])
def test_passes_parent_sampling_decision_in_sampling_context(
    sentry_init, parent_sampling_decision
):
    def dummy_traces_sampler(sampling_context):
        assert sampling_context["parent_sampled"] is parent_sampling_decision
        return 1.0

    sentry_init(traces_sample_rate=1.0, traces_sampler=dummy_traces_sampler)

    sentry_trace_header = (
        "12312012123120121231201212312012-1121201211212012-{sampled}".format(
            sampled=int(parent_sampling_decision)
        )
    )

    with sentry_sdk.continue_trace({"sentry-trace": sentry_trace_header}):
        with sentry_sdk.start_span(name="dogpark"):
            pass


def test_passes_attributes_from_start_span_to_traces_sampler(
    sentry_init, DictionaryContaining  # noqa: N803
):
    traces_sampler = mock.Mock()
    sentry_init(traces_sample_rate=1.0, traces_sampler=traces_sampler)

    with start_span(attributes={"dogs": "yes", "cats": "maybe"}):
        traces_sampler.assert_any_call(
            DictionaryContaining({"dogs": "yes", "cats": "maybe"})
        )


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
        (0, 1),  # wrong type
        {"Maisey": "Charllie"},  # wrong type
        [True, True],  # wrong type
        {0.2012},  # wrong type
        float("NaN"),  # wrong type
        None,  # wrong type
        -1.121,  # wrong value
        1.231,  # wrong value
    ],
)
def test_warns_and_sets_sampled_to_false_on_invalid_traces_sampler_return_value(
    sentry_init, traces_sampler_return_value, StringContaining  # noqa: N803
):
    sentry_init(
        traces_sample_rate=None,
        traces_sampler=mock.Mock(return_value=traces_sampler_return_value),
    )

    with mock.patch.object(logger, "warning", mock.Mock()):
        with start_span(name="dogpark") as span:
            logger.warning.assert_any_call(
                StringContaining("Given sample rate is invalid")
            )
            assert span.sampled is False


@pytest.mark.parametrize(
    "traces_sample_rate,sampled_output,expected_record_lost_event_calls",
    [
        (None, False, []),
        (
            0.0,
            False,
            [("sample_rate", "transaction", None, 1), ("sample_rate", "span", None, 1)],
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
    sentry_init(traces_sample_rate=traces_sample_rate)
    record_lost_event_calls = capture_record_lost_event_calls()

    with start_span(name="dogpark") as span:
        assert span.sampled is sampled_output

    # Use Counter because order of calls does not matter
    assert Counter(record_lost_event_calls) == Counter(expected_record_lost_event_calls)


@pytest.mark.parametrize(
    "traces_sampler,sampled_output,expected_record_lost_event_calls",
    [
        (
            None,
            False,
            [],
        ),
        (
            lambda _x: 0.0,
            False,
            [("sample_rate", "transaction", None, 1), ("sample_rate", "span", None, 1)],
        ),
        (
            lambda _x: 1.0,
            True,
            [],
        ),
    ],
)
def test_records_lost_event_only_if_traces_sampler_enabled(
    sentry_init,
    capture_record_lost_event_calls,
    traces_sampler,
    sampled_output,
    expected_record_lost_event_calls,
):
    sentry_init(
        traces_sample_rate=None,
        traces_sampler=traces_sampler,
    )
    record_lost_event_calls = capture_record_lost_event_calls()

    with start_span(name="dogpark") as span:
        assert span.sampled is sampled_output

    # Use Counter because order of calls does not matter
    assert Counter(record_lost_event_calls) == Counter(expected_record_lost_event_calls)


@pytest.mark.parametrize("parent_sampling_decision", [True, False])
def test_profiles_sampler_gets_sampling_context(sentry_init, parent_sampling_decision):
    def dummy_profiles_sampler(sampling_context):
        assert sampling_context["transaction_context"] == {
            "name": "dogpark",
            "op": "op",
            "source": "custom",
        }
        assert sampling_context["parent_sampled"] == parent_sampling_decision
        return 1.0

    sentry_init(traces_sample_rate=1.0, profiles_sampler=dummy_profiles_sampler)

    sentry_trace = "12312012123120121231201212312012-1121201211212012-{}".format(
        int(parent_sampling_decision)
    )
    with sentry_sdk.continue_trace({"sentry-trace": sentry_trace}):
        with sentry_sdk.start_span(name="dogpark", op="op"):
            pass
