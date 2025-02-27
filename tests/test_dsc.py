"""
This tests test for the correctness of the dynamic sampling context (DSC) in the trace header of envelopes.

The DSC is defined here:
https://develop.sentry.dev/sdk/telemetry/traces/dynamic-sampling-context/#dsc-specification

The DSC is propagated between service using a header called "baggage".
This is not tested in this file.
"""

import random
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

    # We start a new root_span
    with sentry_sdk.start_span(name="foo"):
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

    # We continue the incoming trace and start a new root span
    with sentry_sdk.continue_trace(incoming_http_headers):
        with sentry_sdk.start_span(name="foo"):
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
    with mock.patch.object(random, "random", return_value=0.2):
        with sentry_sdk.continue_trace(incoming_http_headers):
            with sentry_sdk.start_span(name="foo"):
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


@pytest.mark.parametrize(
    "test_data, expected_sample_rate, expected_sampled",
    [
        (
            {
                "incoming_sample_rate": 1.0,
                "incoming_sampled": "true",
                "sentry_trace_header_parent_sampled": 1,
                "use_local_traces_sampler": False,
                "local_traces_sampler_result": None,
                "local_traces_sample_rate": 0.7,
            },
            1.0,  # expected_sample_rate
            "true",  # expected_sampled
        ),
        (
            {
                "incoming_sample_rate": 1.0,
                "incoming_sampled": "true",
                "sentry_trace_header_parent_sampled": 1,
                "use_local_traces_sampler": True,
                "local_traces_sampler_result": 0.5,
                "local_traces_sample_rate": 0.7,
            },
            0.5,  # expected_sample_rate
            "true",  # expected_sampled
        ),
        (
            {
                "incoming_sample_rate": 1.0,
                "incoming_sampled": "false",
                "sentry_trace_header_parent_sampled": 0,
                "use_local_traces_sampler": False,
                "local_traces_sampler_result": None,
                "local_traces_sample_rate": 0.7,
            },
            None,  # expected_sample_rate
            "tracing-disabled-no-transactions-should-be-sent",  # expected_sampled
        ),
        (
            {
                "incoming_sample_rate": 1.0,
                "incoming_sampled": "false",
                "sentry_trace_header_parent_sampled": 0,
                "use_local_traces_sampler": True,
                "local_traces_sampler_result": 0.5,
                "local_traces_sample_rate": 0.7,
            },
            0.5,  # expected_sample_rate
            "false",  # expected_sampled
        ),
        (
            {
                "incoming_sample_rate": 1.0,
                "incoming_sampled": "true",
                "sentry_trace_header_parent_sampled": 1,
                "use_local_traces_sampler": False,
                "local_traces_sampler_result": None,
                "local_traces_sample_rate": None,
            },
            None,  # expected_sample_rate
            "tracing-disabled-no-transactions-should-be-sent",  # expected_sampled
        ),
        (
            {
                "incoming_sample_rate": 1.0,
                "incoming_sampled": "true",
                "sentry_trace_header_parent_sampled": 1,
                "use_local_traces_sampler": True,
                "local_traces_sampler_result": 0.5,
                "local_traces_sample_rate": None,
            },
            0.5,  # expected_sample_rate
            "true",  # expected_sampled
        ),
        (
            {
                "incoming_sample_rate": 1.0,
                "incoming_sampled": "false",
                "sentry_trace_header_parent_sampled": 0,
                "use_local_traces_sampler": False,
                "local_traces_sampler_result": None,
                "local_traces_sample_rate": None,
            },
            None,  # expected_sample_rate
            "tracing-disabled-no-transactions-should-be-sent",  # expected_sampled
        ),
        (
            {
                "incoming_sample_rate": 1.0,
                "incoming_sampled": "false",
                "sentry_trace_header_parent_sampled": 0,
                "use_local_traces_sampler": True,
                "local_traces_sampler_result": 0.5,
                "local_traces_sample_rate": None,
            },
            0.5,  # expected_sample_rate
            "false",  # expected_sampled
        ),
        (
            {
                "incoming_sample_rate": 1.0,
                "incoming_sampled": "true",
                "sentry_trace_header_parent_sampled": None,
                "use_local_traces_sampler": False,
                "local_traces_sampler_result": 0.5,
                "local_traces_sample_rate": 0.7,
            },
            0.7,  # expected_sample_rate
            "true",  # expected_sampled
        ),
    ],
    ids=(
        "1 traces_sample_rate does not override incoming",
        "2 traces_sampler overrides incoming",
        "3 traces_sample_rate does not overrides incoming (incoming not sampled)",
        "4 traces_sampler overrides incoming (incoming not sampled)",
        "5 forwarding incoming (traces_sample_rate not set)",
        "6 traces_sampler overrides incoming  (traces_sample_rate not set)",
        "7 forwarding incoming (traces_sample_rate not set) (incoming not sampled)",
        "8 traces_sampler overrides incoming  (traces_sample_rate not set) (incoming not sampled)",
        "9 traces_sample_rate overrides incoming",
    ),
)
def test_dsc_sample_rate_change(
    sentry_init,
    capture_envelopes,
    test_data,
    expected_sample_rate,
    expected_sampled,
):
    """
    Another service calls our service and passes tracing information to us.
    Our service is continuing the trace, but modifies the sample rate.
    The DSC in transaction envelopes should contain the updated sample rate.
    """

    def my_traces_sampler(sampling_context):
        return test_data["local_traces_sampler_result"]

    init_kwargs = {
        "dsn": "https://mysecret@bla.ingest.sentry.io/12312012",
        "release": "myapp@0.0.1",
        "environment": "canary",
    }

    if test_data["local_traces_sample_rate"]:
        init_kwargs["traces_sample_rate"] = test_data["local_traces_sample_rate"]

    if test_data["use_local_traces_sampler"]:
        init_kwargs["traces_sampler"] = my_traces_sampler

    sentry_init(**init_kwargs)
    envelopes = capture_envelopes()

    # This is what the upstream service sends us
    incoming_trace_id = "771a43a4192642f0b136d5159a501700"
    if test_data["sentry_trace_header_parent_sampled"] is None:
        sentry_trace = f"{incoming_trace_id}-1234567890abcdef"
    else:
        sentry_trace = f"{incoming_trace_id}-1234567890abcdef-{test_data["sentry_trace_header_parent_sampled"]}"

    baggage = (
        f"sentry-trace_id={incoming_trace_id}, "
        f"sentry-sample_rate={str(test_data['incoming_sample_rate'])}, "
        f"sentry-sampled={test_data['incoming_sampled']}, "
        "sentry-public_key=frontendpublickey, "
        "sentry-release=myapp@0.0.1, "
        "sentry-environment=prod, "
        "sentry-transaction=foo, "
    )
    incoming_http_headers = {
        "HTTP_SENTRY_TRACE": sentry_trace,
        "HTTP_BAGGAGE": baggage,
    }

    # We continue the incoming trace and start a new transaction
    with mock.patch.object(random, "random", return_value=0.2):
        with sentry_sdk.continue_trace(incoming_http_headers):
            with sentry_sdk.start_span(name="foo"):
                pass

    if expected_sampled == "tracing-disabled-no-transactions-should-be-sent":
        assert len(envelopes) == 0
    else:
        transaction_envelope = envelopes[0]
        dsc_in_envelope_header = transaction_envelope.headers["trace"]

        assert dsc_in_envelope_header["sample_rate"] == str(expected_sample_rate)
        assert dsc_in_envelope_header["sampled"] == str(expected_sampled).lower()
        assert dsc_in_envelope_header["trace_id"] == incoming_trace_id


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

    # No root span is started, just an error is captured
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
    Our service has tracing enabled and an error occurs in an root span.
    Envelopes containing errors also have the same DSC than the root span envelopes.
    """
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        release="myapp@0.0.1",
        environment="canary",
        traces_sample_rate=1.0,
    )
    envelopes = capture_envelopes()

    # We start a new root span and an error occurs
    with sentry_sdk.start_span(name="foo"):
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
        None,  # no tracing at all. This service will never create root spans.
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
    # but in this test, we do not start a root span.
    with sentry_sdk.continue_trace(incoming_http_headers):

        # No root span is started, just an error is captured
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
