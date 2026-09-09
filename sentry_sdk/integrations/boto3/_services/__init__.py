from functools import lru_cache
from importlib import import_module
from typing import TYPE_CHECKING

from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Tuple

    from sentry_sdk.integrations.boto3._client import _ClientCallContext


class _ServiceExtension:
    """
    Specialize generic botocore client-call instrumentation for a specific
    AWS service, e.g. SQS. `_resolve_service_extension()` caches and shares
    instances across calls, so avoid storing any per-call state this instance.
    """

    # avoid arbitrary attributes by disabling per-instance `__dict__`.
    __slots__ = ()

    def get_span_data(
        self, call_context: "_ClientCallContext"
    ) -> "Optional[Tuple[Optional[str], Optional[str], Dict[str, Any]]]":
        """Returns (operation name, origin, and initial attributes)."""
        return None

    def get_response_span_attributes(
        self, call_context: "_ClientCallContext", response: "Any"
    ) -> "Dict[str, Any]":
        return {}

    def get_error_span_attributes(
        self, call_context: "_ClientCallContext", exception: "BaseException"
    ) -> "Dict[str, Any]":
        return {}

    def inject_trace_context(
        self,
        call_context: "_ClientCallContext",
        api_params: "Any",
        headers: "Dict[str, str]",
    ) -> "Any":
        """Add trace context to a service-specific request payload."""
        return api_params


# store import targets instead of importing service modules eagerly; avoids loading
# service-specific code unnecessarily; service_name -> (module_name, class_name)
_SERVICE_EXTENSIONS: "Dict[str, Tuple[str, str]]" = {
    "dynamodb": (
        "sentry_sdk.integrations.boto3._services.dynamodb",
        "_DynamoDbExtension",
    ),
    "sqs": (
        "sentry_sdk.integrations.boto3._services.sqs",
        "_SqsExtension",
    ),
}


@lru_cache(maxsize=None)
def _resolve_service_extension(
    service_name: str,
) -> "Optional[_ServiceExtension]":
    """Resolves shared extension for a specific AWS service."""
    target = _SERVICE_EXTENSIONS.get(service_name)
    if target is None:
        return None

    extension = None
    with capture_internal_exceptions():
        module_name, class_name = target
        extension_class = getattr(import_module(module_name), class_name)
        extension = extension_class()

    # unkown/broken service falls back to generic instrumentation.
    return extension
