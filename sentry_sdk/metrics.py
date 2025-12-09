"""
NOTE: This file contains experimental code that may be changed or removed at any
time without prior notice.
"""

import time
from typing import Any, Optional, TYPE_CHECKING, Union

import sentry_sdk
from sentry_sdk.utils import safe_repr

if TYPE_CHECKING:
    from sentry_sdk._types import Metric, MetricType


def _capture_metric(
    name: str,
    metric_type: "MetricType",
    value: float,
    unit: "Optional[str]" = None,
    attributes: "Optional[dict[str, Any]]" = None,
) -> None:
    client = sentry_sdk.get_client()

    attrs: "dict[str, Union[str, bool, float, int]]" = {}
    if attributes:
        for k, v in attributes.items():
            attrs[k] = (
                v
                if (
                    isinstance(v, str)
                    or isinstance(v, int)
                    or isinstance(v, bool)
                    or isinstance(v, float)
                )
                else safe_repr(v)
            )

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

    client._capture_metric(metric)


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
