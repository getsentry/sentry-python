import pytest
from unittest import mock

from opentelemetry import trace

import sentry_sdk


tracer = trace.get_tracer(__name__)


@pytest.fixture()
def init_sentry_with_potel(sentry_init):
    def wrapped_sentry_init(*args, **kwargs):
        kwargs.update(
            {
                "_experiments": {"otel_powered_performance": True},
            }
        )
        sentry_init(*args, **kwargs)

    return wrapped_sentry_init


@pytest.mark.parametrize(
    "traces_sampling_rate,expected_num_of_envelopes",
    [
        (-1, 0),  # special case, do not pass any traces_sampling_rate to init()
        (None, 0),
        (0, 0),
        (1, 2),
    ],
)
def test_sampling_traces_sample_rate_0_or_100(
    init_sentry_with_potel,
    capture_envelopes,
    traces_sampling_rate,
    expected_num_of_envelopes,
):
    kwargs = {}
    if traces_sampling_rate != -1:
        kwargs["traces_sample_rate"] = traces_sampling_rate

    init_sentry_with_potel(**kwargs)

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


def test_sampling_traces_sample_rate_50(init_sentry_with_potel, capture_envelopes):
    init_sentry_with_potel(traces_sample_rate=0.5)

    envelopes = capture_envelopes()

    # Make sure random() always returns the same values
    with mock.patch(
        "sentry_sdk.integrations.opentelemetry.sampler.random", side_effect=[0.7, 0.2]
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


def test_sampling_traces_sampler(init_sentry_with_potel, capture_envelopes):
    def keep_only_a(sampling_context):
        if " a" in sampling_context["transaction_context"]["name"]:
            return 0.05
        else:
            return 0

    init_sentry_with_potel(
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


def test_sampling_traces_sampler_boolean(init_sentry_with_potel, capture_envelopes):
    def keep_only_a(sampling_context):
        if " a" in sampling_context["transaction_context"]["name"]:
            return True
        else:
            return False

    init_sentry_with_potel(
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
