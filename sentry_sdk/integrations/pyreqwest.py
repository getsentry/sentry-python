import sentry_sdk
from collections import deque
from contextlib import contextmanager
from sentry_sdk import start_span
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME
from sentry_sdk.tracing_utils import (
    should_propagate_trace,
    add_http_request_source,
    add_sentry_baggage_to_headers,
)
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    logger,
    parse_url,
)

from typing import TYPE_CHECKING
from weakref import WeakSet

if TYPE_CHECKING:
    from typing import Any
    from typing import Iterator


import importlib.util

if importlib.util.find_spec("pyreqwest") is None:
    raise DidNotEnable("pyreqwest is not installed")

_MAX_TRACKED_NON_WEAKREF_BUILDERS = 2048
_instrumented_builders = WeakSet()  # type: "WeakSet[Any]"
_instrumented_non_weakref_builder_ids = set()  # type: "set[int]"
_instrumented_non_weakref_builder_ids_order = deque()  # type: "deque[int]"


class PyreqwestIntegration(Integration):
    identifier = "pyreqwest"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        _patch_pyreqwest()


def _patch_pyreqwest() -> None:
    # Patch Client Builders
    try:
        from pyreqwest.client import ClientBuilder, SyncClientBuilder  # type: ignore[import-not-found]

        _patch_builder_method(ClientBuilder, "build", sentry_async_middleware)
        _patch_builder_method(SyncClientBuilder, "build", sentry_sync_middleware)
    except ImportError:
        pass

    # Patch Request Builders (for simple requests and manual request building)
    try:
        from pyreqwest.request import (  # type: ignore[import-not-found]
            RequestBuilder,
            SyncRequestBuilder,
            OneOffRequestBuilder,
            SyncOneOffRequestBuilder,
        )

        _patch_builder_method(RequestBuilder, "build", sentry_async_middleware)
        _patch_builder_method(RequestBuilder, "build_streamed", sentry_async_middleware)
        _patch_builder_method(SyncRequestBuilder, "build", sentry_sync_middleware)
        _patch_builder_method(
            SyncRequestBuilder, "build_streamed", sentry_sync_middleware
        )
        _patch_builder_method(OneOffRequestBuilder, "send", sentry_async_middleware)
        _patch_builder_method(SyncOneOffRequestBuilder, "send", sentry_sync_middleware)
    except ImportError:
        pass


def _patch_builder_method(cls: type, method_name: str, middleware: "Any") -> None:
    if not hasattr(cls, method_name):
        return

    original_method = getattr(cls, method_name)

    def sentry_patched_method(self: "Any", *args: "Any", **kwargs: "Any") -> "Any":
        if not _builder_is_instrumented(self):
            integration = sentry_sdk.get_client().get_integration(PyreqwestIntegration)
            if integration is not None:
                self.with_middleware(middleware)
                _mark_builder_instrumented(self)
        return original_method(self, *args, **kwargs)

    setattr(cls, method_name, sentry_patched_method)


def _builder_is_instrumented(builder: "Any") -> bool:
    if getattr(builder, "_sentry_instrumented", False):
        return True

    if id(builder) in _instrumented_non_weakref_builder_ids:
        return True

    try:
        return builder in _instrumented_builders
    except TypeError:
        return False


def _mark_builder_instrumented(builder: "Any") -> None:
    try:
        _instrumented_builders.add(builder)
    except TypeError:
        builder_id = id(builder)
        if builder_id not in _instrumented_non_weakref_builder_ids:
            _instrumented_non_weakref_builder_ids.add(builder_id)
            _instrumented_non_weakref_builder_ids_order.append(builder_id)
            if (
                len(_instrumented_non_weakref_builder_ids_order)
                > _MAX_TRACKED_NON_WEAKREF_BUILDERS
            ):
                old_builder_id = _instrumented_non_weakref_builder_ids_order.popleft()
                _instrumented_non_weakref_builder_ids.discard(old_builder_id)

    try:
        builder._sentry_instrumented = True
    except (TypeError, AttributeError):
        pass


@contextmanager
def _sentry_middleware_span(request: "Any") -> "Iterator[Any]":
    parsed_url = None
    with capture_internal_exceptions():
        parsed_url = parse_url(str(request.url), sanitize=False)

    with start_span(
        op=OP.HTTP_CLIENT,
        name="%s %s"
        % (
            request.method,
            parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
        ),
        origin=PyreqwestIntegration.origin,
    ) as span:
        span.set_data(SPANDATA.HTTP_METHOD, request.method)
        if parsed_url is not None:
            span.set_data("url", parsed_url.url)
            span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
            span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

        if should_propagate_trace(sentry_sdk.get_client(), str(request.url)):
            for (
                key,
                value,
            ) in sentry_sdk.get_current_scope().iter_trace_propagation_headers():
                logger.debug(
                    "[Tracing] Adding `{key}` header {value} to outgoing request to {url}.".format(
                        key=key, value=value, url=request.url
                    )
                )

                if key == BAGGAGE_HEADER_NAME:
                    add_sentry_baggage_to_headers(request.headers, value)
                else:
                    request.headers[key] = value

        yield span

    with capture_internal_exceptions():
        add_http_request_source(span)


async def sentry_async_middleware(request: "Any", next_handler: "Any") -> "Any":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return await next_handler.run(request)

    with _sentry_middleware_span(request) as span:
        response = await next_handler.run(request)
        span.set_http_status(response.status)

    return response


def sentry_sync_middleware(request: "Any", next_handler: "Any") -> "Any":
    if sentry_sdk.get_client().get_integration(PyreqwestIntegration) is None:
        return next_handler.run(request)

    with _sentry_middleware_span(request) as span:
        response = next_handler.run(request)
        span.set_http_status(response.status)

    return response
