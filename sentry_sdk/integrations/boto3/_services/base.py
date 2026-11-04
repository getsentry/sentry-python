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

    __slots__ = ()

    def get_span_name(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional span name override, or `None` to keep the default."""
        return None

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.op` override, or `None` to keep the default."""
        return None

    def get_span_origin(self, ctx: "AwsCallContext") -> "Optional[str]":
        """Return an optional `sentry.origin` override, or `None` to keep the default."""
        return None

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        """Return request attributes to add before the AWS request is made."""
        return {}

    def get_span_start_attributes(
        self, ctx: "AwsCallContext", span: "Union[Span, StreamedSpan]"
    ) -> "Attributes":
        """
        Return request attributes that need the started span, e.g. truncated
        `gen_ai` messages. Unlike `get_request_attributes()`, they're added after
        the span starts, so `ignore_spans` doesn't see them.
        """
        return {}

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        """Return response attributes to add after the AWS request is made."""
        return {}

    def instrument_response(
        self,
        ctx: "AwsCallContext",
        span: "Union[Span, StreamedSpan]",
        response: "Any",
    ) -> bool:
        """
        Instrument a response that is read after the AWS request is made, e.g. a
        streaming body. Return `True` if the extension finishes `span` once the
        response is read, or `False` to finish it when the request is made.
        """
        return False
