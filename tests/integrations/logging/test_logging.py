import pytest
import logging

import sentry_sdk

other_logger = logging.getLogger("testfoo")
other_logger.setLevel(logging.DEBUG)

sentry_sdk.get_current_hub().bind_client(sentry_sdk.Client(integrations=['logging']))

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@pytest.mark.parametrize("logger", [logger, other_logger])
def test_logging(capture_events, logger):
    logger.info("bread")
    logger.critical("LOL")
    event, = capture_events
    assert event["level"] == "fatal"
    assert not event["logentry"]["params"]
    assert event["logentry"]["message"] == "LOL"
    assert any(crumb["message"] == "bread" for crumb in event['breadcrumbs'])
