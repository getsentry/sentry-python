from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3._context import AwsCallContext
from sentry_sdk.integrations.boto3._instrumentation import (
    _finish_client_span,
    _sentry_before_sign,
    _sentry_request_created,
    _start_client_span,
)
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Optional, Union

    from sentry_sdk.traces import StreamedSpan
    from sentry_sdk.tracing import Span

try:
    from botocore.client import BaseClient
except ImportError:
    raise DidNotEnable("botocore not installed")


def _patch_botocore_client() -> None:
    orig_init = BaseClient.__init__
    orig_make_api_call = BaseClient._make_api_call  # type: ignore

    def sentry_patched_init(self: "BaseClient", *args: "Any", **kwargs: "Any") -> None:
        orig_init(self, *args, **kwargs)
        meta = self.meta
        meta.events.register("request-created", _sentry_request_created)
        # run after other `before-sign` handlers so existing baggage is preserved.
        meta.events.register_last("before-sign", _sentry_before_sign)

    def sentry_patched_make_api_call(
        self: "BaseClient", operation_name: str, api_params: "Any"
    ) -> "Any":
        """
        Own the span lifecycle for one `_make_api_call()` invocation, including
        all retries performed by botocore, serialization, or endpoint resolution.
        https://github.com/boto/botocore/blob/develop/botocore/client.py
        https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
        """
        client = sentry_sdk.get_client()
        if client.get_integration(Boto3Integration) is None:
            return orig_make_api_call(self, operation_name, api_params)

        ctx = AwsCallContext(operation_name, api_params)
        span: "Optional[Union[Span, StreamedSpan]]" = None

        # add optional metadata to context.
        with capture_internal_exceptions():
            ctx.add_metadata(self)

        with capture_internal_exceptions():
            span = _start_client_span(ctx)
            if span is not None:
                span.__enter__()

        try:
            parsed = orig_make_api_call(self, operation_name, api_params)
        except BaseException as exc:
            if span is not None:
                with capture_internal_exceptions():
                    span.__exit__(type(exc), exc, exc.__traceback__)
            raise

        if span is not None:
            with capture_internal_exceptions():
                _finish_client_span(span, parsed)
        return parsed

    BaseClient.__init__ = sentry_patched_init  # type: ignore
    BaseClient._make_api_call = sentry_patched_make_api_call  # type: ignore
