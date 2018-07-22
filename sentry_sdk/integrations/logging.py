from __future__ import absolute_import
from __future__ import print_function

import logging

from sentry_sdk import get_current_hub, capture_event, add_breadcrumb
from sentry_sdk.utils import to_string, create_event, exceptions_from_error_tuple, skip_internal_frames

class SentryHandler(logging.Handler, object):
    def emit(self, record):
        try:
            self.format(record)
            return self._emit(record)
        except Exception:
            get_current_hub().capture_internal_exception()

    def can_record(self, record):
        return not record.name.startswith('sentry_sdk')

    def _breadcrumb_from_record(self, record):
        return {
            'ty': 'log',
            'level': self._logging_to_event_level(record.levelname),
            'category': record.name,
            'message': record.message
        }

    def _emit(self, record):
        add_breadcrumb(self._breadcrumb_from_record(record))

        if not self._should_create_event(record):
            return

        if not self.can_record(record):
            print(to_string(record.message), file=sys.stderr)
            return

        event = create_event()

        # exc_info might be None or (None, None, None)
        if record.exc_info and all(record.exc_info):
            exc_type, exc_value, tb = record.exc_info
            event['exception'] = exceptions_from_error_tuple(
                exc_type, exc_value, skip_internal_frames(tb),
                get_current_hub().client.options['with_locals']
            )

        event['level'] = self._logging_to_event_level(record.levelname)
        event['logger'] = record.name

        event['logentry'] = {
            'message': to_string(record.msg),
            'params': record.args
        }

        capture_event(event)

    def _logging_to_event_level(self, levelname):
        return {
            'critical': 'fatal'
        }.get(levelname.lower(), levelname.lower())

    def _should_create_event(self, record):
        # TODO: make configurable
        if record.levelno in (logging.ERROR, logging.CRITICAL):
            return True
        return False


HANDLER = SentryHandler()
