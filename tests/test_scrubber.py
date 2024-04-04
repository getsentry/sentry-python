import sys
import logging

from sentry_sdk import capture_exception, capture_event, start_transaction, start_span
from sentry_sdk.utils import event_from_exception
from sentry_sdk.scrubber import EventScrubber
from tests.conftest import ApproxDict


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def test_request_scrubbing(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        1 / 0
    except ZeroDivisionError:
        ev, _hint = event_from_exception(sys.exc_info())

        ev["request"] = {
            "headers": {
                "COOKIE": "secret",
                "authorization": "Bearer bla",
                "ORIGIN": "google.com",
            },
            "cookies": {
                "sessionid": "secret",
                "foo": "bar",
            },
            "data": {
                "token": "secret",
                "foo": "bar",
            },
        }

        capture_event(ev)

    (event,) = events

    assert event["request"] == {
        "headers": {
            "COOKIE": "[Filtered]",
            "authorization": "[Filtered]",
            "ORIGIN": "google.com",
        },
        "cookies": {"sessionid": "[Filtered]", "foo": "bar"},
        "data": {"token": "[Filtered]", "foo": "bar"},
    }

    assert event["_meta"]["request"] == {
        "headers": {
            "COOKIE": {"": {"rem": [["!config", "s"]]}},
            "authorization": {"": {"rem": [["!config", "s"]]}},
        },
        "cookies": {"sessionid": {"": {"rem": [["!config", "s"]]}}},
        "data": {"token": {"": {"rem": [["!config", "s"]]}}},
    }


def test_stack_var_scrubbing(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        password = "supersecret"  # noqa
        api_key = "1231231231"  # noqa
        safe = "keepthis"  # noqa
        1 / 0
    except ZeroDivisionError:
        capture_exception()

    (event,) = events

    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    (frame,) = frames
    assert frame["vars"]["password"] == "[Filtered]"
    assert frame["vars"]["api_key"] == "[Filtered]"
    assert frame["vars"]["safe"] == "'keepthis'"

    meta = event["_meta"]["exception"]["values"]["0"]["stacktrace"]["frames"]["0"][
        "vars"
    ]
    assert meta == {
        "password": {"": {"rem": [["!config", "s"]]}},
        "api_key": {"": {"rem": [["!config", "s"]]}},
    }


def test_breadcrumb_extra_scrubbing(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    logger.info("bread", extra=dict(foo=42, password="secret"))
    logger.critical("whoops", extra=dict(bar=69, auth="secret"))

    (event,) = events

    assert event["extra"]["bar"] == 69
    assert event["extra"]["auth"] == "[Filtered]"

    assert event["breadcrumbs"]["values"][0]["data"] == {
        "foo": 42,
        "password": "[Filtered]",
    }

    assert event["_meta"]["extra"]["auth"] == {"": {"rem": [["!config", "s"]]}}
    assert event["_meta"]["breadcrumbs"] == {
        "values": {"0": {"data": {"password": {"": {"rem": [["!config", "s"]]}}}}}
    }


def test_span_data_scrubbing(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_transaction(name="hi"):
        with start_span(op="foo", description="bar") as span:
            span.set_data("password", "secret")
            span.set_data("datafoo", "databar")

    (event,) = events
    assert event["spans"][0]["data"] == ApproxDict(
        {"password": "[Filtered]", "datafoo": "databar"}
    )
    assert event["_meta"]["spans"] == {
        "0": {"data": {"password": {"": {"rem": [["!config", "s"]]}}}}
    }


def test_custom_denylist(sentry_init, capture_events):
    sentry_init(event_scrubber=EventScrubber(denylist=["my_sensitive_var"]))
    events = capture_events()

    try:
        my_sensitive_var = "secret"  # noqa
        safe = "keepthis"  # noqa
        1 / 0
    except ZeroDivisionError:
        capture_exception()

    (event,) = events

    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    (frame,) = frames
    assert frame["vars"]["my_sensitive_var"] == "[Filtered]"
    assert frame["vars"]["safe"] == "'keepthis'"

    meta = event["_meta"]["exception"]["values"]["0"]["stacktrace"]["frames"]["0"][
        "vars"
    ]
    assert meta == {
        "my_sensitive_var": {"": {"rem": [["!config", "s"]]}},
    }


def test_scrubbing_doesnt_affect_local_vars(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        password = "cat123"
        1 / 0
    except ZeroDivisionError:
        capture_exception()

    (event,) = events

    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    (frame,) = frames
    assert frame["vars"]["password"] == "[Filtered]"
    assert password == "cat123"


def test_recursive_event_scrubber(sentry_init, capture_events):
    sentry_init(event_scrubber=EventScrubber(recursive=True))
    events = capture_events()
    complex_structure = {
        "deep": {
            "deeper": [{"deepest": {"password": "my_darkest_secret"}}],
        },
    }

    capture_event({"extra": complex_structure})

    (event,) = events
    assert event["extra"]["deep"]["deeper"][0]["deepest"]["password"] == "'[Filtered]'"
