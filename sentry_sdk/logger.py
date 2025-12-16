# NOTE: this is the logger sentry exposes to users, not some generic logger.
import functools
import time
from typing import Any

from sentry_sdk import get_client
from sentry_sdk.utils import safe_repr, capture_internal_exceptions

OTEL_RANGES = [
    # ((severity level range), severity text)
    # https://opentelemetry.io/docs/specs/otel/logs/data-model
    ((1, 4), "trace"),
    ((5, 8), "debug"),
    ((9, 12), "info"),
    ((13, 16), "warn"),
    ((17, 20), "error"),
    ((21, 24), "fatal"),
]


class _dict_default_key(dict):  # type: ignore[type-arg]
    """dict that returns the key if missing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _capture_log(
    severity_text: str, severity_number: int, template: str, **kwargs: "Any"
) -> None:
    client = get_client()

    body = template
    attrs: "dict[str, str | bool | float | int]" = {}
    if "attributes" in kwargs:
        attrs.update(kwargs.pop("attributes"))
    for k, v in kwargs.items():
        attrs[f"sentry.message.parameter.{k}"] = v
    if kwargs:
        # only attach template if there are parameters
        attrs["sentry.message.template"] = template

        with capture_internal_exceptions():
            body = template.format_map(_dict_default_key(kwargs))

    attrs = {
        k: (
            v
            if (
                isinstance(v, str)
                or isinstance(v, int)
                or isinstance(v, bool)
                or isinstance(v, float)
            )
            else safe_repr(v)
        )
        for (k, v) in attrs.items()
    }

    # noinspection PyProtectedMember
    client._capture_log(
        {
            "severity_text": severity_text,
            "severity_number": severity_number,
            "attributes": attrs,
            "body": body,
            "time_unix_nano": time.time_ns(),
            "trace_id": None,
            "span_id": None,
        },
    )


trace = functools.partial(_capture_log, "trace", 1)
debug = functools.partial(_capture_log, "debug", 5)
info = functools.partial(_capture_log, "info", 9)
warning = functools.partial(_capture_log, "warn", 13)
error = functools.partial(_capture_log, "error", 17)
fatal = functools.partial(_capture_log, "fatal", 21)


def _otel_severity_text(otel_severity_number: int) -> str:
    for (lower, upper), severity in OTEL_RANGES:
        if lower <= otel_severity_number <= upper:
            return severity

    return "default"


def _log_level_to_otel(level: int, mapping: "dict[Any, int]") -> "tuple[int, str]":
    for py_level, otel_severity_number in sorted(mapping.items(), reverse=True):
        if level >= py_level:
            return otel_severity_number, _otel_severity_text(otel_severity_number)

    return 0, "default"
