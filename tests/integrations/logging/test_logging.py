import pytest
import logging

from sentry_sdk.integrations.logging import HANDLER

logger = logging.getLogger(__name__)

logger.handlers = [HANDLER]
logger.setLevel(logging.DEBUG)

@pytest.mark.parametrize('level', ['info', 'debug', 'warning', 'error'])
def test_logging(capture_events, level):
    getattr(logger, level)('LOL')
    event, = capture_events
    assert event['level'] == level
    assert not event['logentry']['params']
    assert event['logentry']['message'] == 'LOL'
