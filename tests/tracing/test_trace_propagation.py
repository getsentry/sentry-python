import pytest
import requests
import sentry_sdk
from http.client import HTTPConnection

USE_DEFAULT_TRACES_SAMPLE_RATE = -1

INCOMING_TRACE_ID = "771a43a4192642f0b136d5159a501700"
INCOMING_HEADERS = {
    "sentry-trace": f"{INCOMING_TRACE_ID}-1234567890abcdef",
    "baggage": (
        f"sentry-trace_id={INCOMING_TRACE_ID}, "
        "sentry-public_key=frontendpublickey,"
        "sentry-sample_rate=0.01337,"
        "sentry-release=myfrontend,"
        "sentry-environment=bird,"
        "sentry-transaction=bar"
    ),
}


#
# I want to have proper testing of trace propagation.
# Testing the matrix of test cases described here:
# https://docs.google.com/spreadsheets/d/1IyOTYIC2bwu6HeHrxbLHAm6Lq44atVzf2TDJoPCMDZA/edit?gid=0#gid=0
#


@pytest.fixture
def _mock_putheader(monkeypatch):
    """
    Mock HTTPConnection.putheader to capture calls to it.
    """
    putheader_calls = []
    original_putheader = HTTPConnection.putheader

    def mock_putheader_fn(self, header, value):
        putheader_calls.append((header, value))
        return original_putheader(self, header, value)

    monkeypatch.setattr(HTTPConnection, "putheader", mock_putheader_fn)
    return putheader_calls


@pytest.mark.parametrize(
    "traces_sample_rate",
    [
        USE_DEFAULT_TRACES_SAMPLE_RATE,
        None,
        0,
        1,
    ],
    ids=[
        "traces_sample_rate=DEFAULT",
        "traces_sample_rate=None",
        "traces_sample_rate=0",
        "traces_sample_rate=1",
    ],
)
def test_no_incoming_trace_and_trace_propagation_targets_matching(
    sentry_init, capture_events, _mock_putheader, traces_sample_rate
):
    init_kwargs = {}
    if traces_sample_rate != USE_DEFAULT_TRACES_SAMPLE_RATE:
        init_kwargs["traces_sample_rate"] = traces_sample_rate
    sentry_init(**init_kwargs)

    events = capture_events()

    NO_INCOMING_HEADERS = {}  # noqa: N806

    with sentry_sdk.continue_trace(NO_INCOMING_HEADERS):
        with sentry_sdk.start_span(op="test", name="test"):
            requests.get("http://example.com")

    # CHECK if performance data (a transaction/span) is sent to Sentry
    if traces_sample_rate == 1:
        assert len(events) == 1
    else:
        assert len(events) == 0

    outgoing_request_headers = {key: value for key, value in _mock_putheader}

    # CHECK if trace information is added to the outgoing request
    assert "sentry-trace" in outgoing_request_headers
    assert "baggage" in outgoing_request_headers

    # CHECK if incoming trace is continued
    # as no incoming data is given to continue_trace() the incoming trace is never continued
    assert INCOMING_TRACE_ID not in outgoing_request_headers["sentry-trace"]
    assert INCOMING_TRACE_ID not in outgoing_request_headers["baggage"]


@pytest.mark.parametrize(
    "traces_sample_rate",
    [
        USE_DEFAULT_TRACES_SAMPLE_RATE,
        None,
        0,
        1,
    ],
    ids=[
        "traces_sample_rate=DEFAULT",
        "traces_sample_rate=None",
        "traces_sample_rate=0",
        "traces_sample_rate=1",
    ],
)
def test_no_incoming_trace_and_trace_propagation_targets_not_matching(
    sentry_init, capture_events, _mock_putheader, traces_sample_rate
):
    init_kwargs = {
        "trace_propagation_targets": [
            "http://someothersite.com",
        ],
    }
    if traces_sample_rate != USE_DEFAULT_TRACES_SAMPLE_RATE:
        init_kwargs["traces_sample_rate"] = traces_sample_rate
    sentry_init(**init_kwargs)

    events = capture_events()

    NO_INCOMING_HEADERS = {}  # noqa: N806

    with sentry_sdk.continue_trace(NO_INCOMING_HEADERS):
        with sentry_sdk.start_span(op="test", name="test"):
            requests.get("http://example.com")

    # CHECK if performance data (a transaction/span) is sent to Sentry
    if traces_sample_rate == 1:
        assert len(events) == 1
    else:
        assert len(events) == 0

    outgoing_request_headers = {key: value for key, value in _mock_putheader}

    # CHECK if trace information is added to the outgoing request
    assert "sentry-trace" not in outgoing_request_headers
    assert "baggage" not in outgoing_request_headers

    # CHECK if incoming trace is continued
    # (no assert necessary, because the trace information is not added to the outgoing request (see previous asserts))


@pytest.mark.parametrize(
    "traces_sample_rate",
    [
        USE_DEFAULT_TRACES_SAMPLE_RATE,
        None,
        0,
        1,
    ],
    ids=[
        "traces_sample_rate=DEFAULT",
        "traces_sample_rate=None",
        "traces_sample_rate=0",
        "traces_sample_rate=1",
    ],
)
@pytest.mark.parametrize(
    "incoming_parent_sampled",
    ["deferred", "1", "0"],
    ids=[
        "incoming_parent_sampled=DEFERRED",
        "incoming_parent_sampled=1",
        "incoming_parent_sampled=0",
    ],
)
def test_with_incoming_trace_and_trace_propagation_targets_matching(
    sentry_init,
    capture_events,
    _mock_putheader,
    incoming_parent_sampled,
    traces_sample_rate,
):
    init_kwargs = {}
    if traces_sample_rate != USE_DEFAULT_TRACES_SAMPLE_RATE:
        init_kwargs["traces_sample_rate"] = traces_sample_rate
    sentry_init(**init_kwargs)

    events = capture_events()

    incoming_headers = INCOMING_HEADERS.copy()
    if incoming_parent_sampled != "deferred":
        incoming_headers["sentry-trace"] += f"-{incoming_parent_sampled}"
        incoming_headers[
            "baggage"
        ] += f',sentry-sampled={"true" if incoming_parent_sampled == "1" else "false"}'  # noqa: E231

    print("~~~~~~~~~~~~~~~~~~~~")
    print(incoming_headers)
    print("~~~~~~~~~~~~~~~~~~~~")
    with sentry_sdk.continue_trace(incoming_headers):
        with sentry_sdk.start_span(op="test", name="test"):
            requests.get("http://example.com")

    # CHECK if performance data (a transaction/span) is sent to Sentry
    if traces_sample_rate is None:
        assert len(events) == 0
    else:
        if incoming_parent_sampled == "1" or traces_sample_rate == 1:
            assert len(events) == 1
        else:
            assert len(events) == 0

    outgoing_request_headers = {key: value for key, value in _mock_putheader}

    # CHECK if trace information is added to the outgoing request
    assert "sentry-trace" in outgoing_request_headers
    assert "baggage" in outgoing_request_headers

    # CHECK if incoming trace is continued
    if traces_sample_rate in (0, 1, USE_DEFAULT_TRACES_SAMPLE_RATE):
        # continue the incoming trace
        assert INCOMING_TRACE_ID in outgoing_request_headers["sentry-trace"]
        assert INCOMING_TRACE_ID in outgoing_request_headers["baggage"]
    elif traces_sample_rate is None:
        # do NOT continue the incoming trace
        assert INCOMING_TRACE_ID not in outgoing_request_headers["sentry-trace"]
        assert INCOMING_TRACE_ID not in outgoing_request_headers["baggage"]


@pytest.mark.parametrize(
    "traces_sample_rate",
    [
        USE_DEFAULT_TRACES_SAMPLE_RATE,
        None,
        0,
        1,
    ],
    ids=[
        "traces_sample_rate=DEFAULT",
        "traces_sample_rate=None",
        "traces_sample_rate=0",
        "traces_sample_rate=1",
    ],
)
@pytest.mark.parametrize(
    "incoming_parent_sampled",
    ["deferred", "1", "0"],
    ids=[
        "incoming_parent_sampled=DEFERRED",
        "incoming_parent_sampled=1",
        "incoming_parent_sampled=0",
    ],
)
def test_with_incoming_trace_and_trace_propagation_targets_not_matching(
    sentry_init,
    capture_events,
    _mock_putheader,
    incoming_parent_sampled,
    traces_sample_rate,
):
    init_kwargs = {
        "trace_propagation_targets": [
            "http://someothersite.com",
        ],
    }
    if traces_sample_rate != USE_DEFAULT_TRACES_SAMPLE_RATE:
        init_kwargs["traces_sample_rate"] = traces_sample_rate
    sentry_init(**init_kwargs)

    events = capture_events()

    incoming_headers = INCOMING_HEADERS.copy()
    if incoming_parent_sampled != "deferred":
        incoming_headers["sentry-trace"] += f"-{incoming_parent_sampled}"
        incoming_headers[
            "baggage"
        ] += f',sentry-sampled={"true" if incoming_parent_sampled == "1" else "false"}'  # noqa: E231

    with sentry_sdk.continue_trace(incoming_headers):
        with sentry_sdk.start_span(op="test", name="test"):
            requests.get("http://example.com")

    # CHECK if performance data (a transaction/span) is sent to Sentry
    if traces_sample_rate is None:
        assert len(events) == 0
    else:
        if incoming_parent_sampled == "1" or traces_sample_rate == 1:
            assert len(events) == 1
        else:
            assert len(events) == 0

    outgoing_request_headers = {key: value for key, value in _mock_putheader}

    # CHECK if trace information is added to the outgoing request
    assert "sentry-trace" not in outgoing_request_headers
    assert "baggage" not in outgoing_request_headers

    # CHECK if incoming trace is continued
    # (no assert necessary, because the trace information is not added to the outgoing request (see previous asserts))
