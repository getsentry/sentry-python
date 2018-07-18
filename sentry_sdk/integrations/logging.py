from __future__ import absolute_import
from __future__ import print_function

import logging

from sentry_sdk import get_current_hub, capture_event
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

    def _emit(self, record):
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

        event['level'] = record.levelname.lower()
        event['logger'] = record.name

        event['logentry'] = {
            'message': to_string(record.msg),
            'params': record.args
        }

        # TODO: also send formatted message

        capture_event(event)


HANDLER = SentryHandler()
