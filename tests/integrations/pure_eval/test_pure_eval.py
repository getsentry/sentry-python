from types import SimpleNamespace

import pytest

from sentry_sdk import capture_exception, serializer
from sentry_sdk.integrations.pure_eval import PureEvalIntegration


@pytest.mark.parametrize("integrations", [[], [PureEvalIntegration()]])
def test_include_local_variables_enabled(sentry_init, capture_events, integrations):
    sentry_init(include_local_variables=True, integrations=integrations)
    events = capture_events()

    def foo():
        namespace = SimpleNamespace()
        q = 1
        w = 2
        e = 3
        r = 4
        t = 5
        y = 6
        u = 7
        i = 8
        o = 9
        p = 10
        a = 11
        s = 12
        str((q, w, e, r, t, y, u, i, o, p, a, s))  # use variables for linter
        namespace.d = {1: 2}
        print(namespace.d[1] / 0)

        # Appearances of variables after the main statement don't affect order
        print(q)
        print(s)
        print(events)

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
        # Values closest to the exception line appear first
        # Test this order if possible given the Python version and dict order
        expected_keys = [
            "namespace",
            "namespace.d",
            "namespace.d[1]",
            "s",
            "a",
            "p",
            "o",
            "i",
            "u",
            "y",
            "t",
            "r",
            "e",
            "w",
            "q",
            "(q, w, e, r, t, y, u, i, o, p, a, s)",
            "str((q, w, e, r, t, y, u, i, o, p, a, s))",
            "events",
        ]
        assert list(frame_vars.keys()) == expected_keys
        assert frame_vars["namespace.d"] == {"1": "2"}
        assert frame_vars["namespace.d[1]"] == "2"
    else:
        # Without pure_eval, the variables are unpredictable.
        # In later versions, those at the top appear first and are thus included
        assert frame_vars.keys() <= {
            "namespace",
            "q",
            "w",
            "e",
            "r",
            "t",
            "y",
            "u",
            "i",
            "o",
            "p",
            "a",
            "s",
            "events",
        }
        assert len(frame_vars) == 14
