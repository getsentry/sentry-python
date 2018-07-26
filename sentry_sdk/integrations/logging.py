from __future__ import absolute_import
from __future__ import print_function

import sys
import logging
from threading import Lock

from sentry_sdk import get_current_hub, capture_event, add_breadcrumb
from sentry_sdk.utils import to_string, Event, skip_internal_frames


_installer_lock = Lock()
_installed = False
_master_handler = None

def install(client):
    global _installed
    global _master_handler
    with _installer_lock:
        if _installed:
            return

        _master_handler = SentryHandler()
        _master_handler.setLevel(logging.INFO) # TODO: make configurable

        old_callhandlers = logging.Logger.callHandlers

        def sentry_patched_callhandlers(self, record):
            _master_handler.handle(record)
            return old_callhandlers(self, record)

        logging.Logger.callHandlers = sentry_patched_callhandlers

        _installed = True


class SentryHandler(logging.Handler, object):
    def emit(self, record):
        try:
            self.format(record)
            return self._emit(record)
        except Exception:
            get_current_hub().capture_internal_exception()

    def can_record(self, record):
        return not record.name.startswith("sentry_sdk")

    def _breadcrumb_from_record(self, record):
        return {
            "ty": "log",
            "level": self._logging_to_event_level(record.levelname),
            "category": record.name,
            "message": record.message,
        }

    def _emit(self, record):
        add_breadcrumb(self._breadcrumb_from_record(record))

        if not self._should_Event(record):
            return

        if not self.can_record(record):
            print(to_string(record.message), file=sys.stderr)
            return

        event = Event()

        # exc_info might be None or (None, None, None)
        if record.exc_info and all(record.exc_info):
            exc_type, exc_value, tb = record.exc_info
            event.set_exception(
                exc_type,
                exc_value,
                skip_internal_frames(tb),
                get_current_hub().client.options["with_locals"],
            )

        event["level"] = self._logging_to_event_level(record.levelname)
        event["logger"] = record.name

        event["logentry"] = {"message": to_string(record.msg), "params": record.args}

        capture_event(event)

    def _logging_to_event_level(self, levelname):
        return {"critical": "fatal"}.get(levelname.lower(), levelname.lower())

    def _should_Event(self, record):
        # TODO: make configurable
        if record.levelno in (logging.ERROR, logging.CRITICAL):
            return True
        return False
