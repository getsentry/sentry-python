from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext


class _ServiceExtension:
    """
    Specialize generic botocore instrumentation for an AWS service.
    """

    __slots__ = ()

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.op` override for the client span."""
        return None

    def get_span_origin(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.origin` override for the client span."""
        return None

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        """Return service-specific attributes available before the request is made."""
        return {}

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        """Return service-specific attributes derived from the response."""
        return {}
