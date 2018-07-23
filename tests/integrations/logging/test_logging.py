import logging

from sentry_sdk.integrations.logging import HANDLER

logger = logging.getLogger(__name__)

logger.handlers = [HANDLER]
logger.setLevel(logging.DEBUG)


def test_logging(capture_events):
    logger.info("bread")
    logger.critical("LOL")
    event, = capture_events
    assert event["level"] == "fatal"
    assert not event["logentry"]["params"]
    assert event["logentry"]["message"] == "LOL"
    assert event["breadcrumbs"][0]["message"] == "bread"
