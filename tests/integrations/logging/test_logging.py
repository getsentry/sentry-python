import sys

import pytest
import logging

from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger

other_logger = logging.getLogger("testfoo")
logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def reset_level():
    other_logger.setLevel(logging.DEBUG)
    logger.setLevel(logging.DEBUG)


@pytest.mark.parametrize("logger", [logger, other_logger])
def test_logging_works_with_many_loggers(sentry_init, capture_events, logger):
    sentry_init(integrations=[LoggingIntegration(event_level="ERROR")])
    events = capture_events()

    logger.info("bread")
    logger.critical("LOL")
    (event,) = events
    assert event["level"] == "fatal"
    assert not event["logentry"]["params"]
    assert event["logentry"]["message"] == "LOL"
    assert any(crumb["message"] == "bread" for crumb in event["breadcrumbs"]["values"])


@pytest.mark.parametrize("integrations", [None, [], [LoggingIntegration()]])
@pytest.mark.parametrize(
    "kwargs", [{"exc_info": None}, {}, {"exc_info": 0}, {"exc_info": False}]
)
def test_logging_defaults(integrations, sentry_init, capture_events, kwargs):
    sentry_init(integrations=integrations)
    events = capture_events()

    logger.info("bread")
    logger.critical("LOL", **kwargs)
    (event,) = events

    assert event["level"] == "fatal"
    assert any(crumb["message"] == "bread" for crumb in event["breadcrumbs"]["values"])
    assert not any(
        crumb["message"] == "LOL" for crumb in event["breadcrumbs"]["values"]
    )
    assert "threads" not in event


def test_logging_extra_data(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    logger.info("bread", extra=dict(foo=42))
    logger.critical("lol", extra=dict(bar=69))

    (event,) = events

    assert event["level"] == "fatal"
    assert event["extra"] == {"bar": 69}
    assert any(
        crumb["message"] == "bread" and crumb["data"] == {"foo": 42}
        for crumb in event["breadcrumbs"]["values"]
    )


def test_logging_extra_data_integer_keys(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    logger.critical("integer in extra keys", extra={1: 1})

    (event,) = events

    assert event["extra"] == {"1": 1}


@pytest.mark.xfail(sys.version_info[:2] == (3, 4), reason="buggy logging module")
def test_logging_stack(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    logger.error("first", exc_info=True)
    logger.error("second")

    (
        event_with,
        event_without,
    ) = events

    assert event_with["level"] == "error"
    assert event_with["threads"]["values"][0]["stacktrace"]["frames"]

    assert event_without["level"] == "error"
    assert "threads" not in event_without


def test_logging_level(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    logger.setLevel(logging.WARNING)
    logger.error("hi")
    (event,) = events
    assert event["level"] == "error"
    assert event["logentry"]["message"] == "hi"

    del events[:]

    logger.setLevel(logging.ERROR)
    logger.warning("hi")
    assert not events


def test_logging_filters(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    should_log = False

    class MyFilter(logging.Filter):
        def filter(self, record):
            return should_log

    logger.addFilter(MyFilter())
    logger.error("hi")

    assert not events

    should_log = True
    logger.error("hi")

    (event,) = events
    assert event["logentry"]["message"] == "hi"


def test_ignore_logger(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    ignore_logger("testfoo")

    other_logger.error("hi")

    assert not events


def test_ignore_logger_wildcard(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    ignore_logger("testfoo.*")

    nested_logger = logging.getLogger("testfoo.submodule")

    logger.error("hi")

    nested_logger.error("bye")

    (event,) = events
    assert event["logentry"]["message"] == "hi"
