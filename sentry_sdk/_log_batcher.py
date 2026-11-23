from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk.utils import serialize_attribute

if TYPE_CHECKING:
    from typing import Any

    from sentry_sdk._types import Log


def _without_lone_surrogates(value: str) -> str:
    """Replace lone surrogates with U+FFFD and keep valid surrogate pairs.

    json.dumps writes a lone surrogate as a hex escape. Relay rejects that
    escape and drops the whole log container, healthy logs included. Strings
    that are already valid UTF-8 are left untouched.
    """
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return value


def _clean_attribute_value(value: "Any") -> "Any":
    if isinstance(value, str):
        return _without_lone_surrogates(value)
    if isinstance(value, list):
        return [
            _without_lone_surrogates(item) if isinstance(item, str) else item
            for item in value
        ]
    return value


class LogBatcher(Batcher["Log"]):
    MAX_BEFORE_FLUSH = 100
    MAX_BEFORE_DROP = 1_000
    FLUSH_WAIT_TIME = 5.0

    TYPE = "log"
    CLIENT_REPORT_TYPE = "log_item"
    CONTENT_TYPE = "application/vnd.sentry.items.log+json"

    @staticmethod
    def _to_transport_format(item: "Log") -> "Any":
        if "sentry.severity_number" not in item["attributes"]:
            item["attributes"]["sentry.severity_number"] = item["severity_number"]
        if "sentry.severity_text" not in item["attributes"]:
            item["attributes"]["sentry.severity_text"] = item["severity_text"]

        attributes = {}
        for key, value in item["attributes"].items():
            if isinstance(key, str):
                key = _without_lone_surrogates(key)
            serialized = serialize_attribute(value)
            serialized["value"] = _clean_attribute_value(serialized["value"])
            attributes[key] = serialized

        res = {
            "timestamp": int(item["time_unix_nano"]) / 1.0e9,
            "level": str(item["severity_text"]),
            "body": _without_lone_surrogates(str(item["body"])),
            "attributes": attributes,
        }

        if item.get("trace_id") is not None:
            res["trace_id"] = item["trace_id"]

        if item.get("span_id") is not None:
            res["span_id"] = item["span_id"]

        return res
