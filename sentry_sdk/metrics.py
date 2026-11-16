import time
from typing import TYPE_CHECKING, Any, Optional, Sequence

import sentry_sdk
from sentry_sdk._metrics_batcher import SAMPLE_PER_SECOND_KEY, STRATIFY_BY_KEY
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
    stratify_by: "Optional[Sequence[str]]" = None,
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
    if stratify_by:
        # Same, a stratum per distinct value of these attribute keys.
        metric[STRATIFY_BY_KEY] = tuple(stratify_by)  # type: ignore[literal-required]

    sentry_sdk.get_current_scope()._capture_metric(metric)


def count(
    name: str,
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
    sample_per_second: "Optional[float]" = None,
    stratify_by: "Optional[Sequence[str]]" = None,
) -> None:
    _capture_metric(
        name, "counter", value, unit, attributes, sample_per_second, stratify_by
    )


def gauge(
    name: str,
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
    sample_per_second: "Optional[float]" = None,
    stratify_by: "Optional[Sequence[str]]" = None,
) -> None:
    _capture_metric(
        name, "gauge", value, unit, attributes, sample_per_second, stratify_by
    )


def distribution(
    name: str,
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
    sample_per_second: "Optional[float]" = None,
    stratify_by: "Optional[Sequence[str]]" = None,
) -> None:
    _capture_metric(
        name, "distribution", value, unit, attributes, sample_per_second, stratify_by
    )
