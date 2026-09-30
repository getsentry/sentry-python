from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext


class _ServiceExtension:
    """
    Optional hooks for adding service-specific behavior to AWS client
    span; non-overridden methods keep the generic instrumentation.
    Services without a registered extension in `_SERVICE_EXTENSIONS` continue
    to use the generic instrumentation.
    """

    __slots__ = ()

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.op` override, or `None` to keep the default."""
        return None

    def get_span_origin(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.origin` override, or `None` to keep the default."""
        return None

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        """Return request attributes to add before the AWS request is made."""
        return {}

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        """Return response attributes to add after the AWS request is made."""
        return {}
