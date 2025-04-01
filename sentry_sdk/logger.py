# NOTE: this is the logger sentry exposes to users, not some generic logger.
import functools
import time
from typing import Any

from sentry_sdk import get_client, get_current_scope


def _capture_log(severity_text, severity_number, template, **kwargs):
    # type: (str, int, str, **Any) -> None
    client = get_client()
    scope = get_current_scope()

    attrs = {
        "sentry.message.template": template,
    }  # type: dict[str, str | bool | float | int]
    if "attributes" in kwargs:
        attrs.update(kwargs.pop("attributes"))
    for k, v in kwargs.items():
        attrs[f"sentry.message.parameters.{k}"] = (
            v
            if (isinstance(v, str) or isinstance(v, int) or isinstance(v, bool))
            else repr(v)
        )

    # noinspection PyProtectedMember
    client._capture_experimental_log(
        scope,
        {
            "severity_text": severity_text,
            "severity_number": severity_number,
            "attributes": attrs,
            "body": template.format(**kwargs),
            "time_unix_nano": time.time_ns(),
            "trace_id": None,
        },
    )


trace = functools.partial(_capture_log, "trace", 1)
debug = functools.partial(_capture_log, "debug", 5)
info = functools.partial(_capture_log, "info", 9)
warning = functools.partial(_capture_log, "warning", 13)
error = functools.partial(_capture_log, "error", 17)
fatal = functools.partial(_capture_log, "fatal", 21)
