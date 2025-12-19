from typing import TYPE_CHECKING

from sentry_sdk._batcher import Batcher
from sentry_sdk._types import Metric
from sentry_sdk.utils import serialize_attribute
from sentry_sdk.envelope import Item

if TYPE_CHECKING:
    from typing import Any


class MetricsBatcher(Batcher[Metric]):
    MAX_BEFORE_FLUSH = 1000
    MAX_BEFORE_DROP = 10_000
    FLUSH_WAIT_TIME = 5.0

    TYPE = "trace_metric"
    CONTENT_TYPE = "application/vnd.sentry.items.trace-metric+json"

    @staticmethod
    def _to_transport_format(metric: "Metric") -> "Any":
        res = {
            "timestamp": metric["timestamp"],
            "trace_id": metric["trace_id"],
            "name": metric["name"],
            "type": metric["type"],
            "value": metric["value"],
            "attributes": {
                k: serialize_attribute(v) for (k, v) in metric["attributes"].items()
            },
        }

        if metric.get("span_id") is not None:
            res["span_id"] = metric["span_id"]

        if metric.get("unit") is not None:
            res["unit"] = metric["unit"]

        return res

    def _record_lost(self, item: "Metric") -> None:
        self._record_lost_func(
            reason="queue_overflow",
            data_category="trace_metric",
            quantity=1,
        )
