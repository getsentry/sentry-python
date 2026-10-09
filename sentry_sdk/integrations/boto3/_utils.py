from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANDATA

if TYPE_CHECKING:
    from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

    from sentry_sdk._types import Attributes

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
