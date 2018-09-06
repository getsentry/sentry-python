import sys
import logging

from .hub import Hub
from .utils import logger


class _HubBasedClientFilter(logging.Filter):
    def filter(self, record):
        hub = Hub.current
        if hub is not None and hub.client is not None:
            return hub.client.options["debug"]
        return False


def init_debug_support():
    if logger.handlers:
        return
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter(" [sentry] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)
    logger.addFilter(_HubBasedClientFilter())
