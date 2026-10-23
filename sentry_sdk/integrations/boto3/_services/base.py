from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.traces import Span


class _ServiceExtension:
    """
    Optional hooks for adding service-specific behavior to AWS client
    span; non-overridden methods keep the generic instrumentation.
    Services without a registered extension in `_SERVICE_EXTENSIONS` use this
    class as is, i.e. the generic instrumentation.
    """

    __slots__ = ()

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.op` override, or `None` to keep the default."""
        return None

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        """Return request attributes to add before the AWS request is made."""
        return {}

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        """Return response attributes to add after the AWS request is made."""
        return {}

    def get_request_params(
        self, ctx: "AwsCallContext", span: "Optional[Span]"
    ) -> "Optional[Dict[str, Any]]":
        """
        Return params to send instead of `ctx.params`, e.g. with trace propagation added,
        or `None` to send them unchanged; must not mutate `ctx.params`.
        """
        return None

    def has_streaming_response(self, ctx: "AwsCallContext") -> bool:
        """
        Return whether a `StreamingBody` response is read over time, so the span ends
        when it's read or closed, or `False` to end it with the call.
        """
        return True
