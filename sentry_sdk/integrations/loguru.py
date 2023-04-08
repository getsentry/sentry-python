from __future__ import absolute_import

import enum

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.logging import BreadcrumbHandler, EventHandler, BaseHandler

if TYPE_CHECKING:
    from logging import LogRecord
    from typing import Optional, Tuple

try:
    from loguru import logger
except ImportError:
    raise DidNotEnable("LOGURU is not installed")


class LoggingLevels(enum.IntEnum):
    TRACE = 5
    DEBUG = 10
    INFO = 20
    SUCCESS = 25
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


DEFAULT_LEVEL = LoggingLevels.INFO.value
DEFAULT_EVENT_LEVEL = LoggingLevels.ERROR.value
ADDED_HANDLERS = (None, None)  # type: Tuple[Optional[int], Optional[int]]


class LoguruIntegration(Integration):
    identifier = "loguru"

    def __init__(self, level=DEFAULT_LEVEL, event_level=DEFAULT_EVENT_LEVEL):
        # type: (Optional[int], Optional[int]) -> None
        global ADDED_HANDLERS
        breadcrumb_handler, event_handler = ADDED_HANDLERS

        if breadcrumb_handler is not None:
            logger.remove(breadcrumb_handler)
            breadcrumb_handler = None
        if event_handler is not None:
            logger.remove(event_handler)
            event_handler = None

        if level is not None:
            breadcrumb_handler = logger.add(
                LoguruBreadcrumbHandler(level=level), level=level
            )

        if event_level is not None:
            event_handler = logger.add(
                LoguruEventHandler(level=event_level), level=event_level
            )

        ADDED_HANDLERS = (breadcrumb_handler, event_handler)

    @staticmethod
    def setup_once():
        # type: () -> None
        pass


class LoguruBaseHandler(BaseHandler):
    def _logging_to_event_level(self, record):
        # type: (LogRecord) -> str
        try:
            return LoggingLevels(record.levelno).name.lower()
        except ValueError:
            return record.levelname.lower() if record.levelname else ""


class LoguruEventHandler(LoguruBaseHandler, EventHandler):
    pass


class LoguruBreadcrumbHandler(LoguruBaseHandler, BreadcrumbHandler):
    pass
