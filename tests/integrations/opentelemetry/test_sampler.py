import pytest
from unittest import mock

from opentelemetry import trace

import sentry_sdk


tracer = trace.get_tracer(__name__)


@pytest.mark.parametrize(
    "traces_sample_rate, expected_num_of_envelopes",
    [
        # special case for testing, do not pass any traces_sample_rate to init() (the default traces_sample_rate=None will be used)
        (-1, 0),
        # traces_sample_rate=None means do not create new traces, and also do not continue incoming traces. So, no envelopes at all.
        (None, 0),
        # traces_sample_rate=0 means do not create new traces (0% of the requests), but continue incoming traces. So envelopes will be created only if there is an incoming trace.
        (0, 0),
        # traces_sample_rate=1 means create new traces for 100% of requests (and also continue incoming traces, of course).
        (1, 2),
    ],
)
def test_sampling_traces_sample_rate_0_or_100(
    sentry_init,
    capture_envelopes,
    traces_sample_rate,
    expected_num_of_envelopes,
):
    kwargs = {}
    if traces_sample_rate != -1:
        kwargs["traces_sample_rate"] = traces_sample_rate

    sentry_init(**kwargs)

    envelopes = capture_envelopes()

    with sentry_sdk.start_span(description="request a"):
        with sentry_sdk.start_span(description="cache a"):
            with sentry_sdk.start_span(description="db a"):
                ...

    with sentry_sdk.start_span(description="request b"):
        with sentry_sdk.start_span(description="cache b"):
            with sentry_sdk.start_span(description="db b"):
                ...

    assert len(envelopes) == expected_num_of_envelopes

    if expected_num_of_envelopes == 2:
        (transaction_a, transaction_b) = [
            envelope.items[0].payload.json for envelope in envelopes
        ]

        assert transaction_a["transaction"] == "request a"
        assert transaction_b["transaction"] == "request b"

        spans_a = transaction_a["spans"]
        assert len(spans_a) == 2
        assert spans_a[0]["description"] == "cache a"
        assert spans_a[1]["description"] == "db a"
        spans_b = transaction_b["spans"]
        assert len(spans_b) == 2
        assert spans_b[0]["description"] == "cache b"
        assert spans_b[1]["description"] == "db b"


def test_sampling_traces_sample_rate_50(sentry_init, capture_envelopes):
    sentry_init(traces_sample_rate=0.5)

    envelopes = capture_envelopes()

    # Make sure random() always returns the same values
    with mock.patch(
        "sentry_sdk.integrations.opentelemetry.sampler.random",
        side_effect=[0.7, 0.7, 0.7, 0.2, 0.2, 0.2],
    ):
        with sentry_sdk.start_span(description="request a"):
            with sentry_sdk.start_span(description="cache a"):
                with sentry_sdk.start_span(description="db a"):
                    ...

        with sentry_sdk.start_span(description="request b"):
            with sentry_sdk.start_span(description="cache b"):
                with sentry_sdk.start_span(description="db b"):
                    ...

    assert len(envelopes) == 1

    (envelope,) = envelopes
    transaction = envelope.items[0].payload.json
    assert transaction["transaction"] == "request b"
    spans = transaction["spans"]
    assert len(spans) == 2
    assert spans[0]["description"] == "cache b"
    assert spans[1]["description"] == "db b"


def test_sampling_traces_sampler(sentry_init, capture_envelopes):
    def keep_only_a(sampling_context):
        if " a" in sampling_context["transaction_context"]["name"]:
            return 0.05
        else:
            return 0

    sentry_init(
        traces_sample_rate=1.0,
        traces_sampler=keep_only_a,
    )

    envelopes = capture_envelopes()

    # Make sure random() always returns the same values
    with mock.patch(
        "sentry_sdk.integrations.opentelemetry.sampler.random",
        side_effect=[0.04 for _ in range(12)],
    ):

        with sentry_sdk.start_span(description="request a"):  # keep
            with sentry_sdk.start_span(description="cache a"):  # keep
                with sentry_sdk.start_span(description="db a"):  # keep
                    ...

        with sentry_sdk.start_span(description="request b"):  # drop
            with sentry_sdk.start_span(description="cache b"):  # drop
                with sentry_sdk.start_span(description="db b"):  # drop
                    ...

        with sentry_sdk.start_span(description="request c"):  # drop
            with sentry_sdk.start_span(
                description="cache a c"
            ):  # keep (but trx dropped, so not collected)
                with sentry_sdk.start_span(
                    description="db a c"
                ):  # keep (but trx dropped, so not collected)
                    ...

        with sentry_sdk.start_span(description="new a c"):  # keep
            with sentry_sdk.start_span(description="cache c"):  # drop
                with sentry_sdk.start_span(description="db c"):  # drop
                    ...

    assert len(envelopes) == 2
    (envelope1, envelope2) = envelopes
    transaction1 = envelope1.items[0].payload.json
    transaction2 = envelope2.items[0].payload.json

    assert transaction1["transaction"] == "request a"
    assert len(transaction1["spans"]) == 2
    assert transaction2["transaction"] == "new a c"
    assert len(transaction2["spans"]) == 0


def test_sampling_traces_sampler_boolean(sentry_init, capture_envelopes):
    def keep_only_a(sampling_context):
        if " a" in sampling_context["transaction_context"]["name"]:
            return True
        else:
            return False

    sentry_init(
        traces_sample_rate=1.0,
        traces_sampler=keep_only_a,
    )

    envelopes = capture_envelopes()

    with sentry_sdk.start_span(description="request a"):  # keep
        with sentry_sdk.start_span(description="cache a"):  # keep
            with sentry_sdk.start_span(description="db X"):  # drop
                ...

    with sentry_sdk.start_span(description="request b"):  # drop
        with sentry_sdk.start_span(description="cache b"):  # drop
            with sentry_sdk.start_span(description="db b"):  # drop
                ...

    assert len(envelopes) == 1
    (envelope,) = envelopes
    transaction = envelope.items[0].payload.json

    assert transaction["transaction"] == "request a"
    assert len(transaction["spans"]) == 1


@pytest.mark.parametrize(
    "traces_sample_rate, expected_num_of_envelopes",
    [
        # special case for testing, do not pass any traces_sample_rate to init() (the default traces_sample_rate=None will be used)
        (-1, 0),
        # traces_sample_rate=None means do not create new traces, and also do not continue incoming traces. So, no envelopes at all.
        (None, 0),
        # traces_sample_rate=0 means do not create new traces (0% of the requests), but continue incoming traces. So envelopes will be created only if there is an incoming trace.
        (0, 1),
        # traces_sample_rate=1 means create new traces for 100% of requests (and also continue incoming traces, of course).
        (1, 1),
    ],
)
def test_sampling_parent_sampled(
    sentry_init,
    traces_sample_rate,
    expected_num_of_envelopes,
    capture_envelopes,
):
    kwargs = {}
    if traces_sample_rate != -1:
        kwargs["traces_sample_rate"] = traces_sample_rate

    sentry_init(**kwargs)

    envelopes = capture_envelopes()

    # The upstream service has sampled the request
    headers = {
        "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
    }
    with sentry_sdk.continue_trace(headers):
        with sentry_sdk.start_span(description="request a"):
            with sentry_sdk.start_span(description="cache a"):
                with sentry_sdk.start_span(description="db X"):
                    ...

    assert len(envelopes) == expected_num_of_envelopes

    if expected_num_of_envelopes == 1:
        (envelope,) = envelopes
        transaction = envelope.items[0].payload.json
        assert transaction["transaction"] == "request a"
        assert (
            transaction["contexts"]["trace"]["trace_id"]
            == "771a43a4192642f0b136d5159a501700"
        )
        assert transaction["contexts"]["trace"]["span_id"] != "1234567890abcdef"
        assert transaction["contexts"]["trace"]["parent_span_id"] == "1234567890abcdef"


@pytest.mark.parametrize(
    "traces_sample_rate, expected_num_of_envelopes",
    [
        # special case for testing, do not pass any traces_sample_rate to init() (the default traces_sample_rate=None will be used)
        (-1, 0),
        # traces_sample_rate=None means do not create new traces, and also do not continue incoming traces. So, no envelopes at all.
        (None, 0),
        # traces_sample_rate=0 means do not create new traces (0% of the requests), but continue incoming traces. So envelopes will be created only if there is an incoming trace.
        (0, 0),
        # traces_sample_rate=1 means create new traces for 100% of requests (and also continue incoming traces, of course).
        (1, 1),
    ],
)
def test_sampling_parent_dropped(
    sentry_init,
    traces_sample_rate,
    expected_num_of_envelopes,
    capture_envelopes,
):
    kwargs = {}
    if traces_sample_rate != -1:
        kwargs["traces_sample_rate"] = traces_sample_rate

    sentry_init(**kwargs)

    envelopes = capture_envelopes()

    # The upstream service has dropped the request
    headers = {
        "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-0",
    }
    with sentry_sdk.continue_trace(headers):
        with sentry_sdk.start_span(description="request a"):
            with sentry_sdk.start_span(description="cache a"):
                with sentry_sdk.start_span(description="db X"):
                    ...

    assert len(envelopes) == expected_num_of_envelopes

    if expected_num_of_envelopes == 1:
        (envelope,) = envelopes
        transaction = envelope.items[0].payload.json
        assert transaction["transaction"] == "request a"
        assert (
            transaction["contexts"]["trace"]["trace_id"]
            == "771a43a4192642f0b136d5159a501700"
        )
        assert transaction["contexts"]["trace"]["span_id"] != "1234567890abcdef"
        assert transaction["contexts"]["trace"]["parent_span_id"] == "1234567890abcdef"


@pytest.mark.parametrize(
    "traces_sample_rate, expected_num_of_envelopes",
    [
        # special case for testing, do not pass any traces_sample_rate to init() (the default traces_sample_rate=None will be used)
        (-1, 0),
        # traces_sample_rate=None means do not create new traces, and also do not continue incoming traces. So, no envelopes at all.
        (None, 0),
        # traces_sample_rate=0 means do not create new traces (0% of the requests), but continue incoming traces. So envelopes will be created only if there is an incoming trace.
        (0, 0),
        # traces_sample_rate=1 means create new traces for 100% of requests (and also continue incoming traces, of course).
        (1, 1),
    ],
)
def test_sampling_parent_deferred(
    sentry_init,
    traces_sample_rate,
    expected_num_of_envelopes,
    capture_envelopes,
):
    kwargs = {}
    if traces_sample_rate != -1:
        kwargs["traces_sample_rate"] = traces_sample_rate

    sentry_init(**kwargs)

    envelopes = capture_envelopes()

    # The upstream service has deferred the sampling decision to us.
    headers = {
        "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-",
    }

    with sentry_sdk.continue_trace(headers):
        with sentry_sdk.start_span(description="request a"):
            with sentry_sdk.start_span(description="cache a"):
                with sentry_sdk.start_span(description="db X"):
                    ...

    assert len(envelopes) == expected_num_of_envelopes

    if expected_num_of_envelopes == 1:
        (envelope,) = envelopes
        transaction = envelope.items[0].payload.json
        assert transaction["transaction"] == "request a"
        assert (
            transaction["contexts"]["trace"]["trace_id"]
            == "771a43a4192642f0b136d5159a501700"
        )
        assert transaction["contexts"]["trace"]["span_id"] != "1234567890abcdef"
        assert transaction["contexts"]["trace"]["parent_span_id"] == "1234567890abcdef"
