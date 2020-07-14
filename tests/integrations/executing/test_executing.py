# coding: utf-8
import pytest

from sentry_sdk import capture_exception
from sentry_sdk.integrations.executing import ExecutingIntegration


@pytest.mark.parametrize("integrations", [[], [ExecutingIntegration()]])
def test_executing_integration(sentry_init, capture_events, integrations):
    sentry_init(integrations=integrations)
    events = capture_events()

    def foo():
        try:
            bar()
        except Exception:
            capture_exception()

    def bar():
        1 / 0

    foo()

    (event,) = events
    (thread,) = event["exception"]["values"]
    functions = [x["function"] for x in thread["stacktrace"]["frames"]]

    if integrations:
        assert functions == [
            "test_executing_integration.<locals>.foo",
            "test_executing_integration.<locals>.bar",
        ]
        node_texts = []
        for frame in thread["stacktrace"]["frames"]:
            node = frame["executing_node"]
            start = node["start"]
            end = node["end"]
            assert start["line"] == end["line"] == frame["lineno"]
            node_texts.append(frame["context_line"][start["column"] : end["column"]])
        assert node_texts == ["bar()", "1 / 0"]
    else:
        assert functions == ["foo", "bar"]
