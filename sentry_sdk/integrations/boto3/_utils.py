from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.traces import NoOpSpan
from sentry_sdk.tracing_utils import should_propagate_trace

if TYPE_CHECKING:
    from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.traces import Span

    # tuple of (request param name, span attribute name).
    _AttributeSpec = Tuple[str, str]


def _extract_attributes(
    source: "Dict[str, Any]", specs: "Sequence[_AttributeSpec]"
) -> "Attributes":
    attributes = {}
    for param, attribute in specs:
        if param in source:
            attributes[attribute] = source[param]
    return attributes


def _get_aws_arn_attributes(
    values: "Iterable[Optional[str]]", services: "Sequence[str]"
) -> "Attributes":
    """Extract `cloud.account.id` and `cloud.resource_id` from the first matching ARN."""
    for value in values:
        if value is None or not value.startswith("arn:aws"):
            continue
        arn_parts = value.split(":", 5)
        if arn_parts[2] not in services:
            continue

        attributes: "Attributes" = {SPANDATA.CLOUD_RESOURCE_ID: value}
        if arn_parts[4]:
            attributes[SPANDATA.CLOUD_ACCOUNT_ID] = arn_parts[4]
        return attributes

    return {}


def _get_propagation_headers(
    ctx: "AwsCallContext", span: "Optional[Span]"
) -> "Dict[str, str]":
    """Return the `sentry-trace` and `baggage` headers a call propagates to its consumer."""
    if ctx.endpoint_url is None or not should_propagate_trace(
        sentry_sdk.get_client(), ctx.endpoint_url
    ):
        return {}

    # `NoOpSpan` is never sent, use scope's span instead.
    if isinstance(span, NoOpSpan):
        span = None

    return dict(
        sentry_sdk.get_current_scope().iter_trace_propagation_headers(span=span)
    )
