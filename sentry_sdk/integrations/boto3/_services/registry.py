"""Registry for the optional service extensions.

The registry maps botocore service names, such as ``s3``, to extension
classes. It is intentionally static: the number of extensions is small, and
loading service modules dynamically would add complexity for little benefit.

Not every AWS service needs an extension. When a service is not in this map,
the caller receives ``None`` and keeps the generic instrumentation.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Dict, Optional

    from sentry_sdk.integrations.boto3._services.base import _ServiceExtension


# add a ServiceExtension here when one is implemented. for example:
#   _SERVICE_EXTENSIONS = {"s3": _S3Extension()}
# when py 3.15 drops, we might want to take a look at using
# a lazy-loading approach using the new `lazy` keyword.
_SERVICE_EXTENSIONS: "Dict[str, _ServiceExtension]" = {}


def _resolve_service(
    service_name: "Optional[str]",
) -> "Optional[_ServiceExtension]":
    """Return the extension for a service, or `None` for generic instrumentation."""
    if service_name is None:
        return None
    return _SERVICE_EXTENSIONS.get(service_name)
