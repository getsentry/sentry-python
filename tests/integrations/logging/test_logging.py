import logging

import sentry_sdk
from sentry_sdk.integrations.logging import SentryHandler

logger = logging.getLogger(__name__)

logger.handlers = [SentryHandler()]
logger.setLevel(logging.DEBUG)

sentry_sdk.get_current_hub().bind_client(sentry_sdk.Client())


def test_logging(capture_events):
    logger.info("bread")
    logger.critical("LOL")
    event, = capture_events
    assert event["level"] == "fatal"
    assert not event["logentry"]["params"]
    assert event["logentry"]["message"] == "LOL"
    assert event["breadcrumbs"][0]["message"] == "bread"
