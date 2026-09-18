from functools import lru_cache
from importlib import import_module
from typing import TYPE_CHECKING

from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Dict, Optional, Tuple


# service modules are imported lazily.
# e.g. `s3` -> (`sentry_sdk.integrations.boto3._services.s3`, `_S3Extension)
_SERVICE_EXTENSIONS: "Dict[str, Tuple[str, str]]" = {}


@lru_cache(maxsize=None)
def _resolve_service(
    service: "str",
) -> "Optional[_ServiceExtension]":
    target = _SERVICE_EXTENSIONS.get(service)
    if target is None:
        return None

    # preserve generic instrumentation when lookup fails.
    extension = None
    with capture_internal_exceptions():
        module_name, class_name = target
        extension_class = getattr(import_module(module_name), class_name)
        candidate = extension_class()
        if isinstance(candidate, _ServiceExtension):
            extension = candidate

    return extension
