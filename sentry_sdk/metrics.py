import time
from typing import TYPE_CHECKING, Any, Optional

import sentry_sdk
from sentry_sdk._lock_free_metrics_batcher import LockFreeMetricsBatcher
from sentry_sdk.traces import NoOpSpan
from sentry_sdk.utils import format_attribute

if TYPE_CHECKING:
    from sentry_sdk._types import Attributes, Metric, MetricType


def _capture_metric(
    name: str,
    metric_type: "MetricType",
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[Attributes]" = None,
) -> None:
    scope = sentry_sdk.get_current_scope()

    # Window sampling decides first, so metrics it drops skip all the work below
    batcher = scope.get_client().metrics_batcher
    if isinstance(batcher, LockFreeMetricsBatcher):
        span = scope.span
        has_span = span is not None and not isinstance(span, NoOpSpan)
        if not batcher.should_capture(name, has_span):
            return

    attrs: "Attributes" = {}

    if attributes:
        for k, v in attributes.items():
            attrs[k] = format_attribute(v)

    metric: "Metric" = {
        "timestamp": time.time(),
        "trace_id": None,
        "span_id": None,
        "name": name,
        "type": metric_type,
        "value": float(value),
        "unit": unit,
        "attributes": attrs,
    }

    scope._capture_metric(metric)


def count(
    name: str,
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
) -> None:
    _capture_metric(name, "counter", value, unit, attributes)


def gauge(
    name: str,
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
) -> None:
    _capture_metric(name, "gauge", value, unit, attributes)


def distribution(
    name: str,
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
) -> None:
    _capture_metric(name, "distribution", value, unit, attributes)
