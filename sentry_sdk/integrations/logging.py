from __future__ import absolute_import

import logging
import datetime

from sentry_sdk.hub import Hub
from sentry_sdk.utils import (
    to_string,
    event_from_exception,
    capture_internal_exceptions,
)
from sentry_sdk.integrations import Integration

DEFAULT_LEVEL = logging.INFO
DEFAULT_EVENT_LEVEL = logging.ERROR

_IGNORED_LOGGERS = set(["sentry_sdk.errors"])


def ignore_logger(name):
    """This disables the breadcrumb integration for a logger of a specific
    name.  This primary use is for some integrations to disable breadcrumbs
    of this integration.
    """
    _IGNORED_LOGGERS.add(name)


class LoggingIntegration(Integration):
    identifier = "logging"

    def __init__(self, level=DEFAULT_LEVEL, event_level=DEFAULT_EVENT_LEVEL):
        self._handler = None
        self._breadcrumb_handler = None

        if level is not None:
            self._breadcrumb_handler = BreadcrumbHandler(level=level)

        if event_level is not None:
            self._handler = EventHandler(level=event_level)

    def _handle_record(self, record):
        if self._handler is not None and record.levelno >= self._handler.level:
            self._handler.handle(record)

        if (
            self._breadcrumb_handler is not None
            and record.levelno >= self._breadcrumb_handler.level
        ):
            self._breadcrumb_handler.handle(record)

    @staticmethod
    def setup_once():
        old_callhandlers = logging.Logger.callHandlers

        def sentry_patched_callhandlers(self, record):
            # This check is done twice, once also here before we even get
            # the integration.  Otherwise we have a high chance of getting
            # into a recursion error when the integration is resolved
            # (this also is slower).
            if record.name not in _IGNORED_LOGGERS:
                integration = Hub.current.get_integration(LoggingIntegration)
                if integration is not None:
                    integration._handle_record(record)
            return old_callhandlers(self, record)

        logging.Logger.callHandlers = sentry_patched_callhandlers


def _can_record(record):
    return record.name not in _IGNORED_LOGGERS


def _breadcrumb_from_record(record):
    return {
        "ty": "log",
        "level": _logging_to_event_level(record.levelname),
        "category": record.name,
        "message": record.message,
        "timestamp": datetime.datetime.fromtimestamp(record.created),
    }


def _logging_to_event_level(levelname):
    return {"critical": "fatal"}.get(levelname.lower(), levelname.lower())


class EventHandler(logging.Handler, object):
    def emit(self, record):
        with capture_internal_exceptions():
            self.format(record)
            return self._emit(record)

    def _emit(self, record):
        if not _can_record(record):
            return

        hub = Hub.current
        integration = hub.get_integration(LoggingIntegration)
        if integration is None:
            return

        # exc_info might be None or (None, None, None)
        if record.exc_info is not None and record.exc_info[0] is not None:
            event, hint = event_from_exception(
                record.exc_info,
                client_options=hub.client.options,
                mechanism={"type": "logging", "handled": True},
            )
        else:
            event = {}
            hint = None

        event["level"] = _logging_to_event_level(record.levelname)
        event["logger"] = record.name
        event["logentry"] = {"message": to_string(record.msg), "params": record.args}

        hub.capture_event(event, hint=hint)


# Legacy name
SentryHandler = EventHandler


class BreadcrumbHandler(logging.Handler, object):
    def emit(self, record):
        with capture_internal_exceptions():
            self.format(record)
            return self._emit(record)

    def _emit(self, record):
        if not _can_record(record):
            return

        hub = Hub.current
        integration = hub.get_integration(LoggingIntegration)
        if integration is None:
            return

        hub.add_breadcrumb(_breadcrumb_from_record(record), hint={"log_record": record})
