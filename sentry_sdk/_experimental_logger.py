# NOTE: this is the logger sentry exposes to users, not some generic logger.
import functools
from typing import Any

from sentry_sdk import get_client, get_current_scope


def _capture_log(severity_text, severity_number, template, **kwargs):
    # type: (str, int, str, **Any) -> None
    client = get_client()
    scope = get_current_scope()
    client.capture_log(scope, severity_text, severity_number, template, **kwargs)


trace = functools.partial(_capture_log, "trace", 1)
debug = functools.partial(_capture_log, "debug", 5)
info = functools.partial(_capture_log, "info", 9)
warn = functools.partial(_capture_log, "warn", 13)
error = functools.partial(_capture_log, "error", 17)
fatal = functools.partial(_capture_log, "fatal", 21)
