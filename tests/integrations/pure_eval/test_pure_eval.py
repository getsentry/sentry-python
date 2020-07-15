import pytest

from sentry_sdk import capture_exception
from sentry_sdk.integrations.pure_eval import PureEvalIntegration


@pytest.mark.parametrize("integrations", [[], [PureEvalIntegration()]])
def test_with_locals_enabled(sentry_init, capture_events, integrations):
    sentry_init(with_locals=True, integrations=integrations)
    events = capture_events()

    def foo():
        foo.d = {1: 2}
        print(foo.d[1] / 0)

    try:
        foo()
    except Exception:
        capture_exception()

    (event,) = events

    assert all(
        frame["vars"]
        for frame in event["exception"]["values"][0]["stacktrace"]["frames"]
    )

    frame_vars = event["exception"]["values"][0]["stacktrace"]["frames"][-1]["vars"]

    if integrations:
        assert sorted(frame_vars.keys()) == ["foo", "foo.d", "foo.d[1]"]
        assert frame_vars["foo.d"] == {"1": "2"}
        assert frame_vars["foo.d[1]"] == "2"
    else:
        assert sorted(frame_vars.keys()) == ["foo"]
