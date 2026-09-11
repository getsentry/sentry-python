from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Tuple

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext


class _ServiceExtension:
    """
    Specialize generic botocore client-call instrumentation for a specific
    AWS service, e.g. SQS.

    ``_resolve_service_extension()`` caches and shares instances across calls,
    so extensions must remain stateless and receive the per-call context
    explicitly. Boto3 clients are generally thread-safe and may therefore use
    the same extension concurrently:
    https://docs.aws.amazon.com/boto3/latest/guide/clients.html#multithreading-or-multiprocessing-with-clients
    """

    # avoid arbitrary attributes by disabling per-instance `__dict__`.
    __slots__ = ()

    def get_span_config(
        self, ctx: "AwsCallContext"
    ) -> "Optional[Tuple[Optional[str], Optional[str]]]":
        """Return an optional `(op, origin)` override; None defaults to `(HTTP_CLIENT, Boto3Integration.origin)`."""
        return None

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        """Return service-specific attributes derived before the call."""
        return {}

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        """Return service-specific attributes derived from an AWS response."""
        return {}

    def inject_trace_context(
        self,
        ctx: "AwsCallContext",
        trace_context: "Dict[str, str]",
    ) -> "Optional[Dict[str, Any]]":
        """Put the trace context somewhere the consumer of this call can find it."""
        return None
