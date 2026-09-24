from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, Tuple

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext


class _ServiceExtension:
    """
    Specialize generic botocore instrumentation for an AWS service.
    """

    __slots__ = ()

    def get_span_config(
        self, ctx: "AwsCallContext"
    ) -> "Optional[Tuple[Optional[str], Optional[str]]]":
        """Return an optional `(op, origin)` override for the client span."""
        return None

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        """Return service-specific attributes available before the call."""
        return {}

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        """Return service-specific attributes derived from the response."""
        return {}
