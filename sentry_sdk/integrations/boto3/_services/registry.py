from functools import lru_cache
from importlib import import_module
from typing import TYPE_CHECKING

from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Dict, Optional, Tuple


# Store import targets instead of importing service modules eagerly. This keeps
# a broken service extension from disabling generic AWS instrumentation.
_SERVICE_EXTENSIONS: "Dict[str, Tuple[str, str]]" = {
}


@lru_cache(maxsize=None)
def _resolve_service_extension(
    service_name: str,
) -> "Optional[_ServiceExtension]":
    """Resolve a shared extension for a botocore service name."""
    target = _SERVICE_EXTENSIONS.get(service_name)
    if target is None:
        return None

    extension = None
    with capture_internal_exceptions():
        module_name, class_name = target
        extension_class = getattr(import_module(module_name), class_name)
        candidate = extension_class()
        if isinstance(candidate, _ServiceExtension):
            extension = candidate

    # An unknown or broken service extension falls back to generic instrumentation.
    return extension
