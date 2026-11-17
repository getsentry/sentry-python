from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, Union

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.traces import StreamedSpan
    from sentry_sdk.tracing import Span


class _ServiceExtension:
    """
    Optional hooks for adding service-specific behavior to AWS client
    span; non-overridden methods keep the generic instrumentation.
    Services without a registered extension in `_SERVICE_EXTENSIONS` continue
    to use the generic instrumentation.
    """

    __slots__ = "request_start_time"
    skip_if_no_active_span = True

    def __init__(self) -> None:
        self.request_start_time: "Optional[float]" = None

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.op` override, or `None` to keep the default."""
        return None

    def get_span_origin(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.origin` override, or `None` to keep the default."""
        return None

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        """Return request attributes to add before the AWS request is made."""
        return {}

    def on_success_response(
        self,
        ctx: "AwsCallContext",
        response: "Any",
        span: "Union[Span, StreamedSpan]",
    ) -> None:
        """Hook that fires after a successful response. Responsible for closing the span."""
        return None
