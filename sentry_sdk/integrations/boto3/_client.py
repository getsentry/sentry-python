from functools import partial
from typing import TYPE_CHECKING

from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3._instrumentation import (
    _sentry_after_call,
    _sentry_after_call_error,
    _sentry_before_sign,
    _sentry_request_created,
)

if TYPE_CHECKING:
    from typing import Any

try:
    from botocore.client import BaseClient
except ImportError:
    raise DidNotEnable("botocore is not installed")


def _patch_botocore_client() -> None:
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
