from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Optional

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.traces import Span


class _ServiceExtension:
    """
    Optional hooks for adding service-specific behavior to AWS client
    span. Non-overridden methods keep the generic instrumentation.
    """

    __slots__ = ()
    skip_if_no_active_span = True

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.op` override, or `None` to keep the default."""
        return None

    def get_span_name(
        self, ctx: "AwsCallContext", attributes: "Attributes"
    ) -> "Optional[str]":
        """Return an optional span name override, or `None` to keep the default."""
        return None

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        """Return request attributes to add before the AWS request is made."""
        return {}

    def get_request_params(
        self, ctx: "AwsCallContext", span: "Optional[Span]"
    ) -> "Optional[Dict[str, Any]]":
        """
        Return params to send instead of `ctx.params`, e.g. with trace propagation added,
        or `None` to send them unchanged; must not mutate `ctx.params`.
        """
        return None

    def on_success_response(
        self,
        ctx: "AwsCallContext",
        response: "Any",
        span: "Span",
    ) -> None:
        """Hook that fires after a successful response. Responsible for closing the span."""
        return None
