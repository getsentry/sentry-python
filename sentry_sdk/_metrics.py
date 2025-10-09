import time
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sentry_sdk._types import Metric


def _capture_metric(
    name,  # type: str
    metric_type,  # type: str
    value,  # type: float
    unit=None,  # type: Optional[str]
    attributes=None,  # type: Optional[dict[str, Any]]
):
    # type: (...) -> None
    from sentry_sdk.api import get_client, get_current_scope, get_current_span
    from sentry_sdk.utils import safe_repr

    client = get_client()

    attrs = {}  # type: dict[str, str | bool | float | int]
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

    span = get_current_span()
    trace_id = "00000000-0000-0000-0000-000000000000"
    span_id = None

    if span:
        trace_context = span.get_trace_context()
        trace_id = trace_context.get("trace_id", trace_id)
        span_id = trace_context.get("span_id")
    else:
        scope = get_current_scope()
        if scope:
            propagation_context = scope._propagation_context
            if propagation_context:
                trace_id = propagation_context.get("trace_id", trace_id)

    metric = {
        "timestamp": time.time(),
        "trace_id": trace_id,
        "span_id": span_id,
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
