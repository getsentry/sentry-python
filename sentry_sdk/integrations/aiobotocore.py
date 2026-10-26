from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.integrations.boto3._client import (
    _activate_client_span,
    _register_request_hooks,
    _set_error_attributes,
    _set_response_attributes,
    _start_api_call_span,
)
from sentry_sdk.integrations.boto3._instrumentation import (
    _find_streaming_body,
    _finish_span,
    _StreamingBodySpans,
)
from sentry_sdk.integrations.boto3.consts import (
    AIOBOTOCORE_IDENTIFIER,
    AIOBOTOCORE_ORIGIN,
)
from sentry_sdk.traces import NoOpSpan
from sentry_sdk.utils import capture_internal_exceptions, parse_version

if TYPE_CHECKING:
    from typing import Any, Awaitable, Callable, Dict

    from sentry_sdk.traces import Span

try:
    from aiobotocore import __version__ as AIOBOTOCORE_VERSION
    from aiobotocore.client import AioBaseClient
except ImportError:
    raise DidNotEnable("aiobotocore is not installed")


class AioBotocoreIntegration(Integration):
    identifier = AIOBOTOCORE_IDENTIFIER
    origin = AIOBOTOCORE_ORIGIN

    @staticmethod
    def setup_once() -> None:
        version = parse_version(AIOBOTOCORE_VERSION)
        _check_minimum_version(AioBotocoreIntegration, version)

        _patch_aiobotocore_client()


def _instrument_streaming_body(span: "Span", parsed: "Dict[str, Any]") -> bool:
    if isinstance(span, NoOpSpan):
        return False

    # aiobotocore's bodies are botocore `StreamingBody`s since 3.8. Before, they wrap the
    # aiohttp response, which `async with` returns, so their end isn't observable.
    body = _find_streaming_body(parsed)
    if body is None:
        return False

    streaming_spans = _StreamingBodySpans(span, body, AIOBOTOCORE_ORIGIN)

    async def sentry_streaming_body_read(*args: "Any", **kwargs: "Any") -> bytes:
        try:
            read_return_value = await orig_read(*args, **kwargs)
        except BaseException as error:
            streaming_spans.finish(error)
            raise
        with capture_internal_exceptions():
            streaming_spans.finish_if_read(
                args[0] if args else kwargs.get("amt"), read_return_value
            )
        return read_return_value

    def sentry_streaming_body_close(*args: "Any", **kwargs: "Any") -> "Any":
        try:
            # the httpx body's `close()` returns a coroutine.
            close_return_value = orig_close(*args, **kwargs)
        except BaseException as error:
            streaming_spans.finish(error)
            raise
        streaming_spans.finish()
        return close_return_value

    async def sentry_streaming_body_aclose(*args: "Any", **kwargs: "Any") -> None:
        try:
            await orig_aclose(*args, **kwargs)
        except BaseException as error:
            streaming_spans.finish(error)
            raise
        streaming_spans.finish()

    try:
        # aiobotocore's `read()` is a coroutine.
        orig_read: "Callable[..., Awaitable[bytes]]" = body.read  # type: ignore[assignment]
        orig_close = body.close
        orig_aclose = body.aclose  # type: ignore[attr-defined]

        body.read = sentry_streaming_body_read  # type: ignore
        body.close = sentry_streaming_body_close  # type: ignore
        # `async with` closes the body through `aclose()`.
        body.aclose = sentry_streaming_body_aclose  # type: ignore[attr-defined]
    except Exception:
        streaming_spans.finish()
        raise

    return True


def _patch_aiobotocore_client() -> None:
    orig_make_api_call = AioBaseClient._make_api_call

    async def sentry_patched_make_api_call(
        self: "AioBaseClient", operation_name: str, api_params: "Dict[str, Any]"
    ) -> "Any":
        """
        The same lifecycle as boto3's `_make_api_call()`, which aiobotocore overrides
        with a coroutine.
        https://github.com/aio-libs/aiobotocore/blob/3.10.0/aiobotocore/client.py
        """
        client = sentry_sdk.get_client()
        if client.get_integration(AioBotocoreIntegration) is None:
            return await orig_make_api_call(self, operation_name, api_params)

        with capture_internal_exceptions():
            # on every call instead of in `__init__`, which boto3 may have patched to
            # register them as well, and to cover clients created before `init()`.
            _register_request_hooks(self)

        api_call_span = None
        with capture_internal_exceptions():
            api_call_span = _start_api_call_span(
                self, operation_name, api_params, AIOBOTOCORE_ORIGIN
            )

        if api_call_span is None:
            return await orig_make_api_call(self, operation_name, api_params)

        span, ctx, service_ext = api_call_span
        # activate without finishing; a streaming response may outlive the call.
        try:
            with _activate_client_span(span):
                try:
                    parsed = await orig_make_api_call(self, operation_name, api_params)
                except BaseException as error:
                    _set_error_attributes(span, ctx, service_ext, error)
                    raise
                _set_response_attributes(span, ctx, service_ext, parsed)
        except BaseException as error:
            _finish_span(span, error)
            raise

        streaming_body_instrumented = False
        with capture_internal_exceptions():
            streaming_body_instrumented = _instrument_streaming_body(span, parsed)

        # `StreamingBody`s finish their span when consumed or closed.
        if not streaming_body_instrumented:
            _finish_span(span)
        return parsed

    AioBaseClient._make_api_call = sentry_patched_make_api_call
