import sys
import logging

from sentry_sdk import utils
from sentry_sdk.client import _client_init_debug
from sentry_sdk.hub import Hub
from sentry_sdk.scope import Scope
from sentry_sdk.utils import logger
from logging import LogRecord


class _HubBasedClientFilter(logging.Filter):
    def filter(self, record):
        # type: (LogRecord) -> bool
        if _client_init_debug.get(False):
            return True

        return Scope.get_client().options["debug"]


def init_debug_support():
    # type: () -> None
    if not logger.handlers:
        configure_logger()
    configure_debug_hub()


def configure_logger():
    # type: () -> None
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter(" [sentry] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)
    logger.addFilter(_HubBasedClientFilter())


def configure_debug_hub():
    # type: () -> None
    def _get_debug_hub():
        # type: () -> Hub
        return Hub.current

    utils._get_debug_hub = _get_debug_hub
