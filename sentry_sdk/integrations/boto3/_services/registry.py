from typing import TYPE_CHECKING

from sentry_sdk.integrations.boto3._services.s3 import _S3Extension

if TYPE_CHECKING:
    from typing import Dict, Optional

    from sentry_sdk.integrations.boto3._services.base import _ServiceExtension


# add a ServiceExtension here when one is implemented. for example:
#   _SERVICE_EXTENSIONS = {"s3": _S3Extension()}
# when py 3.15 drops, we might want to take a look at using
# a lazy-loading approach using the new `lazy` keyword.
_SERVICE_EXTENSIONS: "Dict[str, _ServiceExtension]" = {
    "s3": _S3Extension(),
}


def _resolve_service(
    service_name: "Optional[str]",
) -> "Optional[_ServiceExtension]":
    """Return the extension for a service, or `None` for generic instrumentation."""
    if service_name is None:
        return None
    return _SERVICE_EXTENSIONS.get(service_name)
