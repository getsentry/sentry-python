import pytest
import requests
import responses
import sentry_sdk


INCOMING_TRACE_ID = "771a43a4192642f0b136d5159a501700"
INCOMING_HEADERS = {
    "sentry-trace": f"{INCOMING_TRACE_ID}-1234567890abcdef-1",
    "baggage": (
        f"sentry-trace_id={INCOMING_TRACE_ID}, "
        "sentry-public_key=frontendpublickey,"
        "sentry-sample_rate=0.01337,"
        "sentry-sampled=true,"
        "sentry-release=myfrontend,"
        "sentry-environment=bird,"
        "sentry-transaction=bar"
    ),
}


#
# I want to have propper testing of trace propagation.
# Testing the matrix of test cases described here:
# https://docs.google.com/spreadsheets/d/1IyOTYIC2bwu6HeHrxbLHAm6Lq44atVzf2TDJoPCMDZA/edit?gid=0#gid=0
#


@responses.activate
@pytest.mark.parametrize(
    "traces_sample_rate",
    [1, 1, 1, 1],  # [-1, None, 0, 1.0],
    ids=[
        "default traces_sample_rate",
        "traces_sample_rate=None",
        "traces_sample_rate=0",
        "traces_sample_rate=1",
    ],
)
def test_trace_propagation_no_incoming_trace(
    sentry_init, capture_events, traces_sample_rate
):
    # TODO: responses mocks away our stdlib putrequest patch, so I need to find a way to mock the request in another way
    responses.add(responses.GET, "http://example.com")

    init_kwargs = {
        "debug": True,
    }
    if traces_sample_rate != -1:
        init_kwargs["traces_sample_rate"] = traces_sample_rate

    sentry_init(**init_kwargs)

    events = capture_events()

    with sentry_sdk.start_span(op="test", name="test"):
        requests.get("http://example.com")

    # CHECK: performance data (a transaction/span) is sent to sentry
    if traces_sample_rate == 1:
        assert len(events) == 1
    else:
        assert len(events) == 0

    # CHECK: trace information is added to the outgoing request
    request = responses.calls[0].request
    if traces_sample_rate == 1:
        assert "sentry-trace" in request.headers
        assert "baggage" in request.headers
        assert INCOMING_TRACE_ID not in request.headers["sentry-trace"]
        assert INCOMING_TRACE_ID not in request.headers["baggage"]
    else:
        assert "sentry-trace" not in request.headers
        assert "baggage" not in request.headers

    # CHECK: the incoming trace_id and the current trace id do match (or not match)
