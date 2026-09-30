from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Dict, Optional

    from sentry_sdk.integrations.boto3._services.base import _ServiceExtension


# when py 3.15 drops, we might want to take a look at using
# a lazy-loading approach using the new `lazy` keyword.
# e.g. {"s3": _S3Extension}
_SERVICE_EXTENSIONS: "Dict[str, _ServiceExtension]" = {}


def _resolve_service(
    service_name: "str",
) -> "Optional[_ServiceExtension]":
    if service_name in _SERVICE_EXTENSIONS:
        return _SERVICE_EXTENSIONS[service_name]
    # preserve generic instrumentation when lookup fails.
    return None
