from functools import partial
from typing import TYPE_CHECKING

from sentry_sdk.integrations import DidNotEnable, _check_minimum_version
from sentry_sdk.integrations.boto3._instrumentation import (
    _sentry_after_call,
    _sentry_after_call_error,
    _sentry_before_sign,
    _sentry_request_created,
)
from sentry_sdk.utils import parse_version

if TYPE_CHECKING:
    from typing import Any

try:
    from botocore import __version__ as BOTOCORE_VERSION
    from botocore.client import BaseClient
except ImportError:
    raise DidNotEnable("botocore is not installed")


def _patch_botocore_client() -> None:
    from sentry_sdk.integrations.boto3 import Boto3Integration

    version = parse_version(BOTOCORE_VERSION)
    _check_minimum_version(Boto3Integration, version, "botocore")

    orig_init = BaseClient.__init__

    def sentry_patched_init(self: "BaseClient", *args: "Any", **kwargs: "Any") -> None:
        orig_init(self, *args, **kwargs)
        meta = self.meta
        service_id = meta.service_model.service_id
        meta.events.register(
            "request-created",
            partial(_sentry_request_created, service_id=service_id),
        )
        # run after other `before-sign` handlers, allowing it to see and preserve existing baggage.
        meta.events.register_last("before-sign", _sentry_before_sign)
        meta.events.register("after-call", _sentry_after_call)
        meta.events.register("after-call-error", _sentry_after_call_error)

    BaseClient.__init__ = sentry_patched_init  # type: ignore
