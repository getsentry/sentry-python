import enum

import sentry_sdk
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.logging import (
    BreadcrumbHandler,
    EventHandler,
    SentryLogsHandler,
    _BaseHandler,
    _python_level_to_otel,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import LogRecord
    from typing import Optional, Any

try:
    import loguru
    from loguru import logger
    from loguru._defaults import LOGURU_FORMAT as DEFAULT_FORMAT
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


SENTRY_LEVEL_FROM_LOGURU_LEVEL = {
    "TRACE": "DEBUG",
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "SUCCESS": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}

DEFAULT_LEVEL = LoggingLevels.INFO.value
DEFAULT_EVENT_LEVEL = LoggingLevels.ERROR.value


class LoguruIntegration(Integration):
    identifier = "loguru"

    level = DEFAULT_LEVEL  # type: Optional[int]
    event_level = DEFAULT_EVENT_LEVEL  # type: Optional[int]
    breadcrumb_format = DEFAULT_FORMAT
    event_format = DEFAULT_FORMAT

    def __init__(
        self,
        level=DEFAULT_LEVEL,
        event_level=DEFAULT_EVENT_LEVEL,
        breadcrumb_format=DEFAULT_FORMAT,
        event_format=DEFAULT_FORMAT,
        sentry_logs_level=DEFAULT_LEVEL,
    ):
        # type: (Optional[int], Optional[int], str | loguru.FormatFunction, str | loguru.FormatFunction, Optional[int]) -> None
        LoguruIntegration.level = level
        LoguruIntegration.event_level = event_level
        LoguruIntegration.breadcrumb_format = breadcrumb_format
        LoguruIntegration.event_format = event_format
        LoguruIntegration.sentry_logs_level = sentry_logs_level

    @staticmethod
    def setup_once():
        # type: () -> None
        if LoguruIntegration.level is not None:
            logger.add(
                LoguruBreadcrumbHandler(level=LoguruIntegration.level),
                level=LoguruIntegration.level,
                format=LoguruIntegration.breadcrumb_format,
            )

        if LoguruIntegration.event_level is not None:
            logger.add(
                LoguruEventHandler(level=LoguruIntegration.event_level),
                level=LoguruIntegration.event_level,
                format=LoguruIntegration.event_format,
            )

        if LoguruIntegration.sentry_logs_level is not None:
            logger.add(
                LoguruSentryLogsHandler(level=LoguruIntegration.sentry_logs_level),
                level=LoguruIntegration.sentry_logs_level,
                format=LoguruIntegration.event_format,
            )


class _LoguruBaseHandler(_BaseHandler):
    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None
        if kwargs.get("level"):
            kwargs["level"] = SENTRY_LEVEL_FROM_LOGURU_LEVEL.get(
                kwargs.get("level", ""), DEFAULT_LEVEL
            )

        super().__init__(*args, **kwargs)

    def _logging_to_event_level(self, record):
        # type: (LogRecord) -> str
        try:
            return SENTRY_LEVEL_FROM_LOGURU_LEVEL[
                LoggingLevels(record.levelno).name
            ].lower()
        except (ValueError, KeyError):
            return record.levelname.lower() if record.levelname else ""


class LoguruEventHandler(_LoguruBaseHandler, EventHandler):
    """Modified version of :class:`sentry_sdk.integrations.logging.EventHandler` to use loguru's level names."""
    pass


class LoguruBreadcrumbHandler(_LoguruBaseHandler, BreadcrumbHandler):
    """Modified version of :class:`sentry_sdk.integrations.logging.BreadcrumbHandler` to use loguru's level names."""
    pass


def _loguru_level_to_otel(record_level):
    # type: (int) -> Tuple[int, str]
    for py_level, otel_severity_number, otel_severity_text in [
        (50, 21, "fatal"),
        (40, 17, "error"),
        (30, 13, "warn"),
        (25, 11, "info"),  # Loguru's success
        (20, 9, "info"),
        (10, 5, "debug"),
        (5, 1, "trace"),
    ]:
        if record_level >= py_level:
            return otel_severity_number, otel_severity_text
    return 0, "default"


class LoguruSentryLogsHandler(_LoguruBaseHandler):
    """Modified version of :class:`sentry_sdk.integrations.logging.SentryLogsHandler` to use loguru's level names."""
    def emit(self, record):
        client = sentry_sdk.get_client()

        if not client.is_active():
            return

        if not client.options["_experiments"].get("enable_logs", False):
            return

        print('rec', record)
        print('strofrec', str(record))

        self._capture_log_from_record(client, record)

    def _capture_log_from_record(self, client, record):
        # type: (BaseClient, LogRecord)
        scope = sentry_sdk.get_current_scope()

        otel_severity_number, otel_severity_text = _python_level_to_otel(record.levelno)

        attrs = {}

        client._capture_experimental_log(
            scope,
            {
                "severity_text": otel_severity_text,
                "severity_number": otel_severity_number,
                "body": record.msg,
                "attributes": attrs,
                "time_unix_nano": int(record.created * 1e9),
                "trace_id": None,
            },
        )
