import time
from typing import TYPE_CHECKING, Any, Optional

import sentry_sdk
from sentry_sdk._metrics_batcher import SAMPLE_PER_SECOND_KEY
from sentry_sdk.utils import format_attribute

if TYPE_CHECKING:
    from sentry_sdk._types import Attributes, Metric, MetricType


def _capture_metric(
    name: str,
    metric_type: "MetricType",
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[Attributes]" = None,
    sample_per_second: "Optional[float]" = None,
) -> None:
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

    if sample_per_second is not None:
        # Only used by the experimental `metrics_window_sampling` option.
        metric[SAMPLE_PER_SECOND_KEY] = sample_per_second  # type: ignore[literal-required]

    sentry_sdk.get_current_scope()._capture_metric(metric)


def count(
    name: str,
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
    sample_per_second: "Optional[float]" = None,
) -> None:
    _capture_metric(name, "counter", value, unit, attributes, sample_per_second)


def gauge(
    name: str,
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
    sample_per_second: "Optional[float]" = None,
) -> None:
    _capture_metric(name, "gauge", value, unit, attributes, sample_per_second)


def distribution(
    name: str,
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
    sample_per_second: "Optional[float]" = None,
) -> None:
    _capture_metric(name, "distribution", value, unit, attributes, sample_per_second)
