import sys

import pytest
import logging

from sentry_sdk.integrations.logging import LoggingIntegration

other_logger = logging.getLogger("testfoo")
other_logger.setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@pytest.mark.parametrize("logger", [logger, other_logger])
def test_logging_works_with_many_loggers(sentry_init, capture_events, logger):
    sentry_init(integrations=[LoggingIntegration(event_level="ERROR")])
    events = capture_events()

    logger.info("bread")
    logger.critical("LOL")
    event, = events
    assert event["level"] == "fatal"
    assert not event["logentry"]["params"]
    assert event["logentry"]["message"] == "LOL"
    assert any(crumb["message"] == "bread" for crumb in event["breadcrumbs"])


@pytest.mark.parametrize("integrations", [None, [], [LoggingIntegration()]])
def test_logging_defaults(integrations, sentry_init, capture_events):
    sentry_init(integrations=integrations)
    events = capture_events()

    logger.info("bread")
    logger.critical("LOL")
    event, = events

    assert event["level"] == "fatal"
    assert any(crumb["message"] == "bread" for crumb in event["breadcrumbs"])
    assert not any(crumb["message"] == "LOL" for crumb in event["breadcrumbs"])
    assert "threads" not in event


def test_logging_extra_data(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    logger.info("bread", extra=dict(foo=42))
    logger.critical("lol", extra=dict(bar=69))

    event, = events

    assert event["level"] == "fatal"
    assert event["extra"] == {"bar": 69}
    assert any(
        crumb["message"] == "bread" and crumb["data"] == {"foo": 42}
        for crumb in event["breadcrumbs"]
    )


@pytest.mark.xfail(sys.version_info[:2] == (3, 4), reason="buggy logging module")
def test_logging_stack(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    logger.error("first", exc_info=True)
    logger.error("second")

    event_with, event_without, = events

    assert event_with["level"] == "error"
    assert event_with["threads"]["values"][0]["stacktrace"]["frames"]

    assert event_without["level"] == "error"
    assert "threads" not in event_without
