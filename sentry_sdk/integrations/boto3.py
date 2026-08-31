from functools import partial
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME
from sentry_sdk.tracing_utils import (
    add_http_breadcrumb,
    add_sentry_baggage_to_headers,
    should_propagate_trace,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    parse_url,
    parse_version,
)

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Type

    from botocore.model import ServiceId

try:
    from botocore import __version__ as BOTOCORE_VERSION
    from botocore.awsrequest import AWSRequest
    from botocore.client import BaseClient
    from botocore.response import StreamingBody
except ImportError:
    raise DidNotEnable("botocore is not installed or incompatible")


class Boto3Integration(Integration):
    identifier = "boto3"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        version = parse_version(BOTOCORE_VERSION)
        _check_minimum_version(Boto3Integration, version, "botocore")

        orig_init = BaseClient.__init__

        def sentry_patched_init(
            self: "BaseClient", *args: "Any", **kwargs: "Any"
        ) -> None:
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


def _sentry_request_created(
    service_id: "ServiceId", request: "AWSRequest", operation_name: str, **kwargs: "Any"
) -> None:
    description = "aws.%s.%s" % (service_id.hyphenize(), operation_name)

    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return

    parsed_url = None
    if request.url is not None:
        with capture_internal_exceptions():
            parsed_url = parse_url(request.url, sanitize=False)

    breadcrumb: "dict[str, Any]" = {}

    span: "Optional[StreamedSpan]" = None
    if parsed_url and should_send_default_pii():
        breadcrumb.update(
            {
                SPANDATA.URL_FULL: parsed_url.url,
                SPANDATA.URL_QUERY: parsed_url.query,
                SPANDATA.URL_FRAGMENT: parsed_url.fragment,
            }
        )

    if request.method is not None:
        breadcrumb[SPANDATA.HTTP_REQUEST_METHOD] = request.method

    if sentry_sdk.traces.get_current_span() is not None:
        span = sentry_sdk.traces.start_span(
            name=description,
            attributes={
                "sentry.op": OP.HTTP_CLIENT,
                "sentry.origin": Boto3Integration.origin,
                SPANDATA.RPC_METHOD: f"{service_id}/{operation_name}",
            },
        )
        if parsed_url and should_send_default_pii():
            span.set_attributes(
                {
                    SPANDATA.URL_FULL: parsed_url.url,
                    SPANDATA.URL_QUERY: parsed_url.query,
                    SPANDATA.URL_FRAGMENT: parsed_url.fragment,
                }
            )

        if request.method is not None:
            span.set_attribute(SPANDATA.HTTP_REQUEST_METHOD, request.method)

    add_http_breadcrumb(None, breadcrumb)

    if span is not None:
        # request.context is an open-ended data-structure
        # where we can add anything useful in request life cycle.
        request.context["_sentrysdk_span"] = span


def _sentry_before_sign(
    request: "AWSRequest", signature_version: "Any", **kwargs: "Any"
) -> None:
    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return

    with capture_internal_exceptions():
        # presigned requests are executed later by another caller. Adding propagation
        # headers here would make those headers part of the signature, requiring the caller to reproduce the same values.
        if isinstance(signature_version, str) and signature_version.endswith(
            ("-query", "-presign-post")
        ):
            return

        if request.url is None or not should_propagate_trace(client, request.url):
            return

        def _replace_header(request: "AWSRequest", key: str, value: str) -> None:
            """
            Botocore's `HTTPHeaders` inherits from `email.message.Message`, where:
                headers["foo"] = "old"
                headers["foo"] = "new"
            produces two fields: {"foo": "old", "foo": "new"}. So delete existing
            fields before assigning replacement.
            """
            if key in request.headers:
                del request.headers[key]
            request.headers[key] = value

        # use span associated with this botocore request
        span = request.context.get("_sentrysdk_span")

        headers = sentry_sdk.get_current_scope().iter_trace_propagation_headers(
            span=span
        )
        for header_name, header_value in headers:
            if header_name != BAGGAGE_HEADER_NAME:
                # normal headers (e.g. `sentry-trace`) are non-shared, so replace stale values
                _replace_header(request, header_name, header_value)
                continue

            # merge existing `baggage` values under single header
            existing_values = request.headers.get_all(BAGGAGE_HEADER_NAME, [])
            combined_baggage = {
                BAGGAGE_HEADER_NAME: ",".join(str(value) for value in existing_values)
            }
            # preserve third-party baggage, replace stale `sentry-*` values
            add_sentry_baggage_to_headers(combined_baggage, header_value)
            _replace_header(
                request, BAGGAGE_HEADER_NAME, combined_baggage[BAGGAGE_HEADER_NAME]
            )


def _sentry_after_call(
    context: "Dict[str, Any]", parsed: "Dict[str, Any]", **kwargs: "Any"
) -> None:
    span: "Optional[StreamedSpan]" = context.pop("_sentrysdk_span", None)

    # Span could be absent if the integration is disabled.
    if span is None:
        return

    span.__exit__(None, None, None)

    body = parsed.get("Body")
    if not isinstance(body, StreamingBody):
        return

    streaming_span = sentry_sdk.traces.start_span(
        name=span.name,
        parent_span=span,
        attributes={
            "sentry.op": OP.HTTP_CLIENT_STREAM,
            "sentry.origin": Boto3Integration.origin,
        },
    )

    orig_read = body.read
    orig_close = body.close

    def sentry_streaming_body_read(*args: "Any", **kwargs: "Any") -> bytes:
        try:
            ret = orig_read(*args, **kwargs)
            if ret:
                return ret

            streaming_span.end()
            return ret
        except Exception:
            streaming_span.end()
            raise

    body.read = sentry_streaming_body_read  # type: ignore

    def sentry_streaming_body_close(*args: "Any", **kwargs: "Any") -> None:
        streaming_span.end()
        orig_close(*args, **kwargs)

    body.close = sentry_streaming_body_close  # type: ignore


def _sentry_after_call_error(
    context: "Dict[str, Any]", exception: "Type[BaseException]", **kwargs: "Any"
) -> None:
    span: "Optional[StreamedSpan]" = context.pop("_sentrysdk_span", None)

    # Span could be absent if the integration is disabled.
    if span is None:
        return

    span.__exit__(type(exception), exception, None)
