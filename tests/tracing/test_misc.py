from unittest.mock import MagicMock

import pytest

import sentry_sdk
from sentry_sdk.consts import MATCH_ALL
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import should_propagate_trace
from sentry_sdk.utils import Dsn


def test_finds_segment_on_scope(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    with sentry_sdk.traces.start_span(name="dogpark"):
        scope = sentry_sdk.get_current_scope()
        assert scope.streamed_span is not None
        assert isinstance(scope.streamed_span, StreamedSpan)
        assert scope.streamed_span.name == "dogpark"

        assert scope._span is not None
        assert isinstance(scope._span, StreamedSpan)
        assert scope._span.name == "dogpark"


def test_finds_span_on_scope(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    segment = sentry_sdk.traces.start_span(name="dogpark")
    sentry_sdk.traces.start_span(name="sniffing", parent_span=segment)

    scope = sentry_sdk.get_current_scope()

    assert scope._span is not None
    assert isinstance(scope._span, StreamedSpan)
    assert scope._span.name == "sniffing"


@pytest.mark.parametrize(
    "trace_propagation_targets,url,expected_propagation_decision",
    [
        (None, "http://example.com", False),
        ([], "http://example.com", False),
        ([MATCH_ALL], "http://example.com", True),
        (["localhost"], "http://localhost:8443/api/users", True),
        (["localhost"], "mylocalhost:8080/api/users", True),
        ([r"^/api"], "/api/envelopes", True),
        ([r"^/api"], "/backend/api/envelopes", False),
        ([r"myApi.com/v[2-4]"], "myApi.com/v2/projects", True),
        ([r"myApi.com/v[2-4]"], "myApi.com/v1/projects", False),
        ([r"https://.*"], "https://example.com", True),
        ([r"https://.*"], "http://example.com/insecure/", False),
    ],
)
def test_should_propagate_trace(
    trace_propagation_targets, url, expected_propagation_decision
):
    client = MagicMock()

    # This test assumes the urls are not Sentry URLs. Use test_should_propagate_trace_to_sentry for sentry URLs.
    client.is_sentry_url = lambda _: False

    client.options = {"trace_propagation_targets": trace_propagation_targets}
    client.transport = MagicMock()
    client.transport.parsed_dsn = Dsn("https://bla@xxx.sentry.io/12312012")

    assert should_propagate_trace(client, url) == expected_propagation_decision


@pytest.mark.parametrize(
    "dsn,url,expected_propagation_decision",
    [
        (
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            "http://example.com",
            True,
        ),
        (
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            False,
        ),
        (
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            "http://squirrelchasers.ingest.sentry.io/12312012",
            False,
        ),
        (
            "https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
            "http://ingest.sentry.io/12312012",
            True,
        ),
        (
            "https://abc@localsentry.example.com/12312012",
            "http://localsentry.example.com",
            False,
        ),
    ],
)
def test_should_propagate_trace_to_sentry(
    sentry_init, dsn, url, expected_propagation_decision
):
    sentry_init(
        dsn=dsn,
        traces_sample_rate=1.0,
    )

    client = sentry_sdk.get_client()
    client.transport.parsed_dsn = Dsn(dsn)

    assert should_propagate_trace(client, url) == expected_propagation_decision


def test_start_transaction_updates_scope_name(sentry_init):
    sentry_init(traces_sample_rate=1.0, trace_lifecycle="stream")

    scope = sentry_sdk.get_current_scope()

    with sentry_sdk.traces.start_span(
        name="foobar", attributes={"sentry.segment.name.source": "test"}
    ):
        assert scope._transaction == "foobar"
        assert scope._transaction_info == {"source": "test"}
