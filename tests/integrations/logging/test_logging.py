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
    assert not events

    sentry_sdk.capture_exception(ValueError())
    event, = events

    assert event["level"] == "error"
    assert any(crumb["message"] == "bread" for crumb in event["breadcrumbs"])
    assert any(crumb["message"] == "LOL" for crumb in event["breadcrumbs"])
