from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk.utils import serialize_attribute

if TYPE_CHECKING:
    from typing import Any

    from sentry_sdk._types import Log, SerializedAttributeValue


def _sanitize_surrogates(s: str) -> str:
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _serialize_and_sanitize_attribute(val: "Any") -> "SerializedAttributeValue":
    serialized = serialize_attribute(val)
    s_val = serialized["value"]
    if isinstance(s_val, str):
        serialized["value"] = _sanitize_surrogates(s_val)
    elif isinstance(s_val, list):
        serialized["value"] = [
            _sanitize_surrogates(x) if isinstance(x, str) else x for x in s_val
        ]
    return serialized


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

        res = {
            "timestamp": int(item["time_unix_nano"]) / 1.0e9,
            "level": str(item["severity_text"]),
            "body": _sanitize_surrogates(str(item["body"])),
            "attributes": {
                _sanitize_surrogates(str(k)): _serialize_and_sanitize_attribute(v)
                for (k, v) in item["attributes"].items()
            },
        }

        if item.get("trace_id") is not None:
            res["trace_id"] = item["trace_id"]

        if item.get("span_id") is not None:
            res["span_id"] = item["span_id"]

        return res
