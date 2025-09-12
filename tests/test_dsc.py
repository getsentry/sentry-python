"""
This tests test for the correctness of the dynamic sampling context (DSC) in the trace header of envelopes.

The DSC is defined here:
https://develop.sentry.dev/sdk/telemetry/traces/dynamic-sampling-context/#dsc-specification

The DSC is propagated between service using a header called "baggage".
This is not tested in this file.
"""

from unittest import mock

import pytest

import sentry_sdk
import sentry_sdk.client


def test_dsc_head_of_trace(sentry_init, capture_envelopes):
    """
    Our service is the head of the trace (it starts a new trace)
    and sends a transaction event to Sentry.
    """
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        release="myapp@0.0.1",
        environment="canary",
        traces_sample_rate=1.0,
    )
    envelopes = capture_envelopes()

    # We start a new transaction
    with sentry_sdk.start_transaction(name="foo"):
        pass

    assert len(envelopes) == 1

    transaction_envelope = envelopes[0]
    envelope_trace_header = transaction_envelope.headers["trace"]

    assert "trace_id" in envelope_trace_header
    assert type(envelope_trace_header["trace_id"]) == str

    assert "public_key" in envelope_trace_header
    assert type(envelope_trace_header["public_key"]) == str
    assert envelope_trace_header["public_key"] == "mysecret"

    assert "sample_rate" in envelope_trace_header
    assert type(envelope_trace_header["sample_rate"]) == str
    assert envelope_trace_header["sample_rate"] == "1.0"

    assert "sampled" in envelope_trace_header
    assert type(envelope_trace_header["sampled"]) == str
    assert envelope_trace_header["sampled"] == "true"

    assert "release" in envelope_trace_header
    assert type(envelope_trace_header["release"]) == str
    assert envelope_trace_header["release"] == "myapp@0.0.1"

    assert "environment" in envelope_trace_header
    assert type(envelope_trace_header["environment"]) == str
    assert envelope_trace_header["environment"] == "canary"

    assert "transaction" in envelope_trace_header
    assert type(envelope_trace_header["transaction"]) == str
    assert envelope_trace_header["transaction"] == "foo"


def test_dsc_continuation_of_trace(sentry_init, capture_envelopes):
    """
    Another service calls our service and passes tracing information to us.
    Our service is continuing the trace and sends a transaction event to Sentry.
    """
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        release="myapp@0.0.1",
        environment="canary",
        traces_sample_rate=1.0,
    )
    envelopes = capture_envelopes()

    # This is what the upstream service sends us
    sentry_trace = "771a43a4192642f0b136d5159a501700-1234567890abcdef-1"
    baggage = (
        "other-vendor-value-1=foo;bar;baz, "
        "sentry-trace_id=771a43a4192642f0b136d5159a501700, "
        "sentry-public_key=frontendpublickey, "
        "sentry-sample_rate=0.01337, "
        "sentry-sampled=true, "
        "sentry-release=myfrontend@1.2.3, "
        "sentry-environment=bird, "
        "sentry-transaction=bar, "
        "other-vendor-value-2=foo;bar;"
    )
    incoming_http_headers = {
        "HTTP_SENTRY_TRACE": sentry_trace,
        "HTTP_BAGGAGE": baggage,
    }

    # We continue the incoming trace and start a new transaction
    transaction = sentry_sdk.continue_trace(incoming_http_headers)
    with sentry_sdk.start_transaction(transaction, name="foo"):
        pass

    assert len(envelopes) == 1

    transaction_envelope = envelopes[0]
    envelope_trace_header = transaction_envelope.headers["trace"]

    assert "trace_id" in envelope_trace_header
    assert type(envelope_trace_header["trace_id"]) == str
    assert envelope_trace_header["trace_id"] == "771a43a4192642f0b136d5159a501700"

    assert "public_key" in envelope_trace_header
    assert type(envelope_trace_header["public_key"]) == str
    assert envelope_trace_header["public_key"] == "frontendpublickey"

    assert "sample_rate" in envelope_trace_header
    assert type(envelope_trace_header["sample_rate"]) == str
    assert envelope_trace_header["sample_rate"] == "1.0"

    assert "sampled" in envelope_trace_header
    assert type(envelope_trace_header["sampled"]) == str
    assert envelope_trace_header["sampled"] == "true"

    assert "release" in envelope_trace_header
    assert type(envelope_trace_header["release"]) == str
    assert envelope_trace_header["release"] == "myfrontend@1.2.3"

    assert "environment" in envelope_trace_header
    assert type(envelope_trace_header["environment"]) == str
    assert envelope_trace_header["environment"] == "bird"

    assert "transaction" in envelope_trace_header
    assert type(envelope_trace_header["transaction"]) == str
    assert envelope_trace_header["transaction"] == "bar"


def test_dsc_continuation_of_trace_sample_rate_changed_in_traces_sampler(
    sentry_init, capture_envelopes
):
    """
    Another service calls our service and passes tracing information to us.
    Our service is continuing the trace, but modifies the sample rate.
    The DSC propagated further should contain the updated sample rate.
    """

    def my_traces_sampler(sampling_context):
        return 0.25

    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        release="myapp@0.0.1",
        environment="canary",
        traces_sampler=my_traces_sampler,
    )
    envelopes = capture_envelopes()

    # This is what the upstream service sends us
    sentry_trace = "771a43a4192642f0b136d5159a501700-1234567890abcdef-1"
    baggage = (
        "other-vendor-value-1=foo;bar;baz, "
        "sentry-trace_id=771a43a4192642f0b136d5159a501700, "
        "sentry-public_key=frontendpublickey, "
        "sentry-sample_rate=1.0, "
        "sentry-sampled=true, "
        "sentry-release=myfrontend@1.2.3, "
        "sentry-environment=bird, "
        "sentry-transaction=bar, "
        "other-vendor-value-2=foo;bar;"
    )
    incoming_http_headers = {
        "HTTP_SENTRY_TRACE": sentry_trace,
        "HTTP_BAGGAGE": baggage,
    }

    # We continue the incoming trace and start a new transaction
    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=125000):
        transaction = sentry_sdk.continue_trace(incoming_http_headers)
        with sentry_sdk.start_transaction(transaction, name="foo"):
            pass

    assert len(envelopes) == 1

    transaction_envelope = envelopes[0]
    envelope_trace_header = transaction_envelope.headers["trace"]

    assert "trace_id" in envelope_trace_header
    assert type(envelope_trace_header["trace_id"]) == str
    assert envelope_trace_header["trace_id"] == "771a43a4192642f0b136d5159a501700"

    assert "public_key" in envelope_trace_header
    assert type(envelope_trace_header["public_key"]) == str
    assert envelope_trace_header["public_key"] == "frontendpublickey"

    assert "sample_rate" in envelope_trace_header
    assert type(envelope_trace_header["sample_rate"]) == str
    assert envelope_trace_header["sample_rate"] == "0.25"

    assert "sampled" in envelope_trace_header
    assert type(envelope_trace_header["sampled"]) == str
    assert envelope_trace_header["sampled"] == "true"

    assert "release" in envelope_trace_header
    assert type(envelope_trace_header["release"]) == str
    assert envelope_trace_header["release"] == "myfrontend@1.2.3"

    assert "environment" in envelope_trace_header
    assert type(envelope_trace_header["environment"]) == str
    assert envelope_trace_header["environment"] == "bird"

    assert "transaction" in envelope_trace_header
    assert type(envelope_trace_header["transaction"]) == str
    assert envelope_trace_header["transaction"] == "bar"


def test_dsc_issue(sentry_init, capture_envelopes):
    """
    Our service is a standalone service that does not have tracing enabled. Just uses Sentry for error reporting.
    """
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        release="myapp@0.0.1",
        environment="canary",
    )
    envelopes = capture_envelopes()

    # No transaction is started, just an error is captured
    try:
        1 / 0
    except ZeroDivisionError as exp:
        sentry_sdk.capture_exception(exp)

    assert len(envelopes) == 1

    error_envelope = envelopes[0]

    envelope_trace_header = error_envelope.headers["trace"]

    assert "trace_id" in envelope_trace_header
    assert type(envelope_trace_header["trace_id"]) == str

    assert "public_key" in envelope_trace_header
    assert type(envelope_trace_header["public_key"]) == str
    assert envelope_trace_header["public_key"] == "mysecret"

    assert "sample_rate" not in envelope_trace_header

    assert "sampled" not in envelope_trace_header

    assert "release" in envelope_trace_header
    assert type(envelope_trace_header["release"]) == str
    assert envelope_trace_header["release"] == "myapp@0.0.1"

    assert "environment" in envelope_trace_header
    assert type(envelope_trace_header["environment"]) == str
    assert envelope_trace_header["environment"] == "canary"

    assert "transaction" not in envelope_trace_header


def test_dsc_issue_with_tracing(sentry_init, capture_envelopes):
    """
    Our service has tracing enabled and an error occurs in an transaction.
    Envelopes containing errors also have the same DSC than the transaction envelopes.
    """
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        release="myapp@0.0.1",
        environment="canary",
        traces_sample_rate=1.0,
    )
    envelopes = capture_envelopes()

    # We start a new transaction and an error occurs
    with sentry_sdk.start_transaction(name="foo"):
        try:
            1 / 0
        except ZeroDivisionError as exp:
            sentry_sdk.capture_exception(exp)

    assert len(envelopes) == 2

    error_envelope, transaction_envelope = envelopes

    assert error_envelope.headers["trace"] == transaction_envelope.headers["trace"]

    envelope_trace_header = error_envelope.headers["trace"]

    assert "trace_id" in envelope_trace_header
    assert type(envelope_trace_header["trace_id"]) == str

    assert "public_key" in envelope_trace_header
    assert type(envelope_trace_header["public_key"]) == str
    assert envelope_trace_header["public_key"] == "mysecret"

    assert "sample_rate" in envelope_trace_header
    assert envelope_trace_header["sample_rate"] == "1.0"
    assert type(envelope_trace_header["sample_rate"]) == str

    assert "sampled" in envelope_trace_header
    assert type(envelope_trace_header["sampled"]) == str
    assert envelope_trace_header["sampled"] == "true"

    assert "release" in envelope_trace_header
    assert type(envelope_trace_header["release"]) == str
    assert envelope_trace_header["release"] == "myapp@0.0.1"

    assert "environment" in envelope_trace_header
    assert type(envelope_trace_header["environment"]) == str
    assert envelope_trace_header["environment"] == "canary"

    assert "transaction" in envelope_trace_header
    assert type(envelope_trace_header["transaction"]) == str
    assert envelope_trace_header["transaction"] == "foo"


@pytest.mark.parametrize(
    "traces_sample_rate",
    [
        0,  # no traces will be started, but if incoming traces will be continued (by our instrumentations, not happening in this test)
        None,  # no tracing at all. This service will never create transactions.
    ],
)
def test_dsc_issue_twp(sentry_init, capture_envelopes, traces_sample_rate):
    """
    Our service does not have tracing enabled, but we receive tracing information from an upstream service.
    Error envelopes still contain a DCS. This is called "tracing without performance" or TWP for short.

    This way if I have three services A, B, and C, and A and C have tracing enabled, but B does not,
    we still can see the full trace in Sentry, and associate errors send by service B to Sentry.
    (This test would be service B in this scenario)
    """
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        release="myapp@0.0.1",
        environment="canary",
        traces_sample_rate=traces_sample_rate,
    )
    envelopes = capture_envelopes()

    # This is what the upstream service sends us
    sentry_trace = "771a43a4192642f0b136d5159a501700-1234567890abcdef-1"
    baggage = (
        "other-vendor-value-1=foo;bar;baz, "
        "sentry-trace_id=771a43a4192642f0b136d5159a501700, "
        "sentry-public_key=frontendpublickey, "
        "sentry-sample_rate=0.01337, "
        "sentry-sampled=true, "
        "sentry-release=myfrontend@1.2.3, "
        "sentry-environment=bird, "
        "sentry-transaction=bar, "
        "other-vendor-value-2=foo;bar;"
    )
    incoming_http_headers = {
        "HTTP_SENTRY_TRACE": sentry_trace,
        "HTTP_BAGGAGE": baggage,
    }

    # We continue the trace (meaning: saving the incoming trace information on the scope)
    # but in this test, we do not start a transaction.
    sentry_sdk.continue_trace(incoming_http_headers)

    # No transaction is started, just an error is captured
    try:
        1 / 0
    except ZeroDivisionError as exp:
        sentry_sdk.capture_exception(exp)

    assert len(envelopes) == 1

    error_envelope = envelopes[0]

    envelope_trace_header = error_envelope.headers["trace"]

    assert "trace_id" in envelope_trace_header
    assert type(envelope_trace_header["trace_id"]) == str
    assert envelope_trace_header["trace_id"] == "771a43a4192642f0b136d5159a501700"

    assert "public_key" in envelope_trace_header
    assert type(envelope_trace_header["public_key"]) == str
    assert envelope_trace_header["public_key"] == "frontendpublickey"

    assert "sample_rate" in envelope_trace_header
    assert type(envelope_trace_header["sample_rate"]) == str
    assert envelope_trace_header["sample_rate"] == "0.01337"

    assert "sampled" in envelope_trace_header
    assert type(envelope_trace_header["sampled"]) == str
    assert envelope_trace_header["sampled"] == "true"

    assert "release" in envelope_trace_header
    assert type(envelope_trace_header["release"]) == str
    assert envelope_trace_header["release"] == "myfrontend@1.2.3"

    assert "environment" in envelope_trace_header
    assert type(envelope_trace_header["environment"]) == str
    assert envelope_trace_header["environment"] == "bird"

    assert "transaction" in envelope_trace_header
    assert type(envelope_trace_header["transaction"]) == str
    assert envelope_trace_header["transaction"] == "bar"
