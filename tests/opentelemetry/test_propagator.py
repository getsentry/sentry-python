from unittest.mock import MagicMock, patch

import pytest

from opentelemetry.trace.propagation import get_current_span
from opentelemetry.propagators.textmap import DefaultSetter
from opentelemetry.semconv.trace import SpanAttributes

import sentry_sdk
from sentry_sdk.consts import MATCH_ALL
from sentry_sdk.opentelemetry.consts import (
    SENTRY_BAGGAGE_KEY,
    SENTRY_TRACE_KEY,
)
from sentry_sdk.opentelemetry import SentryPropagator
from tests.conftest import SortedBaggage


@pytest.mark.forked
def test_extract_no_context_no_sentry_trace_header():
    """
    No context and NO Sentry trace data in getter.
    Extract should return empty context.
    """
    carrier = None
    context = {}
    getter = MagicMock()
    getter.get.return_value = None

    modified_context = SentryPropagator().extract(carrier, context, getter)

    assert modified_context == {}


@pytest.mark.forked
def test_extract_context_no_sentry_trace_header():
    """
    Context but NO Sentry trace data in getter.
    Extract should return context as is.
    """
    carrier = None
    context = {"some": "value"}
    getter = MagicMock()
    getter.get.return_value = None

    modified_context = SentryPropagator().extract(carrier, context, getter)

    assert modified_context == context


@pytest.mark.forked
def test_extract_empty_context_sentry_trace_header_no_baggage():
    """
    Empty context but Sentry trace data but NO Baggage in getter.
    Extract should return context that has empty baggage in it and also a NoopSpan with span_id and trace_id.
    """
    carrier = None
    context = {}
    getter = MagicMock()
    getter.get.side_effect = [
        ["1234567890abcdef1234567890abcdef-1234567890abcdef-1"],
        None,
    ]

    modified_context = SentryPropagator().extract(carrier, context, getter)

    assert len(modified_context.keys()) == 3

    assert modified_context[SENTRY_TRACE_KEY] == {
        "trace_id": "1234567890abcdef1234567890abcdef",
        "parent_span_id": "1234567890abcdef",
        "parent_sampled": True,
    }
    assert modified_context[SENTRY_BAGGAGE_KEY].serialize() == ""

    span_context = get_current_span(modified_context).get_span_context()
    assert span_context.span_id == int("1234567890abcdef", 16)
    assert span_context.trace_id == int("1234567890abcdef1234567890abcdef", 16)


@pytest.mark.forked
def test_extract_context_sentry_trace_header_baggage():
    """
    Empty context but Sentry trace data and Baggage in getter.
    Extract should return context that has baggage in it and also a NoopSpan with span_id and trace_id.
    """
    baggage_header = (
        "other-vendor-value-1=foo;bar;baz, sentry-trace_id=771a43a4192642f0b136d5159a501700, "
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
        "sentry-user_id=Am%C3%A9lie, other-vendor-value-2=foo;bar;"
    )

    carrier = None
    context = {"some": "value"}
    getter = MagicMock()
    getter.get.side_effect = [
        ["1234567890abcdef1234567890abcdef-1234567890abcdef-1"],
        [baggage_header],
    ]

    modified_context = SentryPropagator().extract(carrier, context, getter)

    assert len(modified_context.keys()) == 4

    assert modified_context[SENTRY_TRACE_KEY] == {
        "trace_id": "1234567890abcdef1234567890abcdef",
        "parent_span_id": "1234567890abcdef",
        "parent_sampled": True,
    }

    assert modified_context[SENTRY_BAGGAGE_KEY].serialize() == (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
        "sentry-sample_rate=0.01337,sentry-user_id=Am%C3%A9lie"
    )

    span_context = get_current_span(modified_context).get_span_context()
    assert span_context.span_id == int("1234567890abcdef", 16)
    assert span_context.trace_id == int("1234567890abcdef1234567890abcdef", 16)


def test_inject_continue_trace(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    carrier = {}
    setter = DefaultSetter()

    trace_id = "771a43a4192642f0b136d5159a501700"
    sentry_trace = "771a43a4192642f0b136d5159a501700-1234567890abcdef-1"
    baggage = (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
        "sentry-public_key=frontendpublickey,"
        "sentry-sample_rate=0.01337,"
        "sentry-sampled=true,"
        "sentry-release=myfrontend,"
        "sentry-environment=bird,"
        "sentry-transaction=bar"
    )
    incoming_headers = {
        "HTTP_SENTRY_TRACE": sentry_trace,
        "HTTP_BAGGAGE": baggage,
    }

    expected_baggage = baggage + ",sentry-sample_rand=0.001111"

    with patch(
        "sentry_sdk.tracing_utils.Random.uniform",
        return_value=0.001111,
    ):
        with sentry_sdk.continue_trace(incoming_headers):
            with sentry_sdk.start_span(name="foo") as span:
                SentryPropagator().inject(carrier, setter=setter)
                assert carrier["sentry-trace"] == f"{trace_id}-{span.span_id}-1"
                assert carrier["baggage"] == SortedBaggage(expected_baggage)


def test_inject_continue_trace_incoming_sample_rand(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    carrier = {}
    setter = DefaultSetter()

    trace_id = "771a43a4192642f0b136d5159a501700"
    sentry_trace = "771a43a4192642f0b136d5159a501700-1234567890abcdef-1"
    baggage = (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
        "sentry-public_key=frontendpublickey,"
        "sentry-sample_rate=0.01337,"
        "sentry-sampled=true,"
        "sentry-release=myfrontend,"
        "sentry-environment=bird,"
        "sentry-transaction=bar,"
        "sentry-sample_rand=0.002849"
    )
    incoming_headers = {
        "HTTP_SENTRY_TRACE": sentry_trace,
        "HTTP_BAGGAGE": baggage,
    }

    with sentry_sdk.continue_trace(incoming_headers):
        with sentry_sdk.start_span(name="foo") as span:
            SentryPropagator().inject(carrier, setter=setter)
            assert carrier["sentry-trace"] == f"{trace_id}-{span.span_id}-1"
            assert carrier["baggage"] == SortedBaggage(baggage)


def test_inject_head_sdk(sentry_init):
    sentry_init(traces_sample_rate=1.0, release="release")

    carrier = {}
    setter = DefaultSetter()

    expected_baggage = (
        "sentry-transaction=foo,"
        "sentry-release=release,"
        "sentry-environment=production,"
        "sentry-trace_id={trace_id},"
        "sentry-sample_rate=1.0,"
        "sentry-sampled=true,"
        "sentry-sample_rand=0.111111"
    )

    with patch(
        "sentry_sdk.tracing_utils.Random.uniform",
        return_value=0.111111,
    ):
        with sentry_sdk.start_span(name="foo") as span:
            SentryPropagator().inject(carrier, setter=setter)
            assert carrier["sentry-trace"] == f"{span.trace_id}-{span.span_id}-1"
            assert carrier["baggage"] == SortedBaggage(
                expected_baggage.format(trace_id=span.trace_id)
            )


@pytest.mark.parametrize(
    "trace_propagation_targets,url,trace_propagated",
    [
        # No targets - should not propagate
        ([], "https://example.com/api/users", False),
        (None, "https://example.com/api/users", False),
        # MATCH_ALL - should propagate
        ([MATCH_ALL], "https://example.com/api/users", True),
        # Exact match - should propagate
        (["https://example.com"], "https://example.com/api/users", True),
        (["https://example.com/"], "https://example.com/api/users", True),
        # No match - should not propagate
        (["https://example.com"], "https://other-domain.com/api/users", False),
        (["https://example.com/"], "https://other-domain.com/api/users", False),
        # Regex patterns
        (
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://good.example.net/api",
            True,
        ),
        (
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "https://example.net/api",
            False,
        ),
        # HTTP vs HTTPS
        (["https://example.com"], "http://example.com/api/users", False),
        (["http://example.com"], "https://example.com/api/users", False),
        # Path matching
        (["https://example.com/api"], "https://example.com/api/users", True),
        (["https://example.com/api"], "https://example.com/other/path", False),
    ],
)
def test_propagator_trace_propagation_targets(
    sentry_init,
    trace_propagation_targets,
    url,
    trace_propagated,
):
    """Test that the propagator respects trace_propagation_targets for HTTP spans."""
    sentry_init(
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
    )

    carrier = {}
    setter = DefaultSetter()

    # Create a real HTTP span with the test URL
    with sentry_sdk.start_span(name="http.client") as span:
        span.set_attribute(SpanAttributes.HTTP_METHOD, "GET")
        span.set_attribute(SpanAttributes.HTTP_URL, url)

        # Test the propagator
        SentryPropagator().inject(carrier, setter=setter)

        if trace_propagated:
            assert "sentry-trace" in carrier
            assert "baggage" in carrier
        else:
            assert "sentry-trace" not in carrier
            assert "baggage" not in carrier
