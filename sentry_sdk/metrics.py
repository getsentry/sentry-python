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
    name,  # type: str
    metric_type,  # type: MetricType
    value,  # type: float
    unit=None,  # type: Optional[str]
    attributes=None,  # type: Optional[dict[str, Any]]
):
    # type: (...) -> None
    client = sentry_sdk.get_client()

    attrs = {}  # type: dict[str, Union[str, bool, float, int]]
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

    metric = {
        "timestamp": time.time(),
        "trace_id": None,
        "span_id": None,
        "name": name,
        "type": metric_type,
        "value": float(value),
        "unit": unit,
        "attributes": attrs,
    }  # type: Metric

    client._capture_metric(metric)


def count(
    name,  # type: str
    value,  # type: float
    unit=None,  # type: Optional[str]
    attributes=None,  # type: Optional[dict[str, Any]]
):
    # type: (...) -> None
    _capture_metric(name, "counter", value, unit, attributes)


def gauge(
    name,  # type: str
    value,  # type: float
    unit=None,  # type: Optional[str]
    attributes=None,  # type: Optional[dict[str, Any]]
):
    # type: (...) -> None
    _capture_metric(name, "gauge", value, unit, attributes)


def distribution(
    name,  # type: str
    value,  # type: float
    unit=None,  # type: Optional[str]
    attributes=None,  # type: Optional[dict[str, Any]]
):
    # type: (...) -> None
    _capture_metric(name, "distribution", value, unit, attributes)
