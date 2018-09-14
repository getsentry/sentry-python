from __future__ import absolute_import
from __future__ import print_function

import logging
import datetime

from sentry_sdk.utils import (
    to_string,
    event_from_exception,
    capture_internal_exceptions,
)
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration


_IGNORED_LOGGERS = set(["sentry_sdk.errors"])


def ignore_logger(name):
    """This disables the breadcrumb integration for a logger of a specific
    name.  This primary use is for some integrations to disable breadcrumbs
    of this integration.
    """
    _IGNORED_LOGGERS.add(name)


class LoggingIntegration(Integration):
    identifier = "logging"

    def __init__(self, level=logging.INFO, event_level=None):
        self._handler = SentryHandler(level=level, event_level=event_level)

    def install(self):
        handler = self._handler

        old_callhandlers = logging.Logger.callHandlers

        def sentry_patched_callhandlers(self, record):
            handler.handle(record)
            return old_callhandlers(self, record)

        logging.Logger.callHandlers = sentry_patched_callhandlers


class SentryHandler(logging.Handler, object):
    def __init__(self, level, event_level):
        logging.Handler.__init__(self, level)
        if event_level is None:
            self._event_level = None
        else:
            self._event_level = logging._checkLevel(event_level)

    def emit(self, record):
        with capture_internal_exceptions():
            self.format(record)
            return self._emit(record)

    def can_record(self, record):
        return record.name not in _IGNORED_LOGGERS

    def _breadcrumb_from_record(self, record):
        return {
            "ty": "log",
            "level": self._logging_to_event_level(record.levelname),
            "category": record.name,
            "message": record.message,
            "timestamp": datetime.datetime.fromtimestamp(record.created),
        }

    def _emit(self, record):
        if not self.can_record(record):
            return

        hub = Hub.current
        if hub.client is None:
            return

        if self._should_create_event(record):
            with capture_internal_exceptions():
                hint = None
                # exc_info might be None or (None, None, None)
                if record.exc_info is not None and record.exc_info[0] is not None:
                    event, hint = event_from_exception(
                        record.exc_info,
                        with_locals=hub.client.options["with_locals"],
                        mechanism={"type": "logging", "handled": True},
                    )
                else:
                    event = {}

                event["level"] = self._logging_to_event_level(record.levelname)
                event["logger"] = record.name
                event["logentry"] = {
                    "message": to_string(record.msg),
                    "params": record.args,
                }

                hub.capture_event(event, hint=hint)

        with capture_internal_exceptions():
            hub.add_breadcrumb(
                self._breadcrumb_from_record(record), hint={"log_record": record}
            )

    def _logging_to_event_level(self, levelname):
        return {"critical": "fatal"}.get(levelname.lower(), levelname.lower())

    def _should_create_event(self, record):
        return self._event_level is not None and record.levelno >= self._event_level
