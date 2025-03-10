# NOTE: this is the logger sentry exposes to users, not some generic logger.
import functools
from typing import Any, Optional

from sentry_sdk import get_client, get_current_scope


def capture_log(severity_text, severity_number, template, **kwargs):
    # type: (str, int, str, **Any) -> Optional[str]
    client = get_client()
    scope = get_current_scope()
    return client.capture_log(scope, severity_text, severity_number, template, **kwargs)


trace = functools.partial(capture_log, "trace", 1)
debug = functools.partial(capture_log, "debug", 5)
info = functools.partial(capture_log, "info", 9)
warn = functools.partial(capture_log, "warn", 13)
error = functools.partial(capture_log, "error", 17)
fatal = functools.partial(capture_log, "fatal", 21)
