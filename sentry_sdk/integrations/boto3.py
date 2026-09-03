from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME, Span
from sentry_sdk.tracing_utils import (
    add_http_breadcrumb,
    add_sentry_baggage_to_headers,
    get_url_attributes,
    has_span_streaming_enabled,
    should_propagate_trace,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    parse_url,
    parse_version,
)

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Union

    from botocore.model import ServiceId


try:
    from botocore import __version__ as BOTOCORE_VERSION
    from botocore.awsrequest import AWSRequest
    from botocore.client import BaseClient
    from botocore.response import StreamingBody
except ImportError:
    raise DidNotEnable("botocore is not installed")


class _ClientCallContext:
    """Inputs and client metadata for one botocore client call."""

    __slots__ = (
        "client",
        "service_name",
        "service_id",
        "operation_name",
        "region_name",
        "endpoint_url",
        "api_version",
        "api_params",
    )

    def __init__(
        self,
        client: "BaseClient",
        operation_name: str,
        api_params: "Any",
    ) -> None:
        client_meta = client.meta
        service_model = client_meta.service_model

        self.client: "BaseClient" = client
        # botocore's internal identifier, e.g. "apigateway".
        self.service_name: str = service_model.service_name
        # modeled display identity, e.g. "API gateway".
        self.service_id: "ServiceId" = service_model.service_id
        self.operation_name: str = operation_name
        self.region_name: "Optional[str]" = getattr(client_meta, "region_name", None)
        self.endpoint_url: str = client_meta.endpoint_url
        self.api_version: str = service_model.api_version
        # params may contain streams or arbitrary sdk objects; make shallow copy
        # so nested values preserve original identity.
        self.api_params: "Dict[str, Any]" = (
            dict(api_params) if isinstance(api_params, dict) else {}
        )


class Boto3Integration(Integration):
    identifier = "boto3"
    origin = f"auto.http.{identifier}"

    @staticmethod
    def setup_once() -> None:
        version = parse_version(BOTOCORE_VERSION)
        _check_minimum_version(Boto3Integration, version, "botocore")

        orig_init = BaseClient.__init__
        orig_make_api_call = BaseClient._make_api_call  # type: ignore

        def sentry_patched_init(
            self: "BaseClient", *args: "Any", **kwargs: "Any"
        ) -> None:
            orig_init(self, *args, **kwargs)
            meta = self.meta
            meta.events.register("request-created", _sentry_request_created)
            # run after other `before-sign` handlers, allowing it to see and preserve existing baggage.
            meta.events.register_last("before-sign", _sentry_before_sign)

        def sentry_patched_make_api_call(
            self: "BaseClient", operation_name: str, api_params: "Any"
        ) -> "Any":
            """
            Own the client span lifecycle for one `_make_api_call()` invocation. The span covers
            the entire client-side lifecycle, including all retries performed by botocore.
            https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
            """
            client = sentry_sdk.get_client()
            if client.get_integration(Boto3Integration) is None:
                return orig_make_api_call(self, operation_name, api_params)

            span: "Optional[Union[Span, StreamedSpan]]" = None

            with capture_internal_exceptions():
                call_context = _ClientCallContext(self, operation_name, api_params)
                span = _start_client_span(call_context)
                if span is not None:
                    span.__enter__()

            try:
                parsed = orig_make_api_call(self, operation_name, api_params)
            except BaseException as exc:
                if span is not None:
                    with capture_internal_exceptions():
                        _finish_client_span_with_error(span, exc)
                raise

            if span is not None:
                with capture_internal_exceptions():
                    _finish_client_span(span, parsed)
            return parsed

        BaseClient.__init__ = sentry_patched_init  # type: ignore
        BaseClient._make_api_call = sentry_patched_make_api_call  # type: ignore


def _start_client_span(
    call_context: "_ClientCallContext",
) -> "Optional[Union[Span, StreamedSpan]]":
    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return None

    description = "aws.%s.%s" % (
        call_context.service_id.hyphenize(),
        call_context.operation_name,
    )

    if has_span_streaming_enabled(client.options):
        if sentry_sdk.traces.get_current_span() is None:
            return None

        return sentry_sdk.traces.start_span(
            name=description,
            attributes={
                SPANDATA.SENTRY_OP: OP.HTTP_CLIENT,
                SPANDATA.SENTRY_ORIGIN: Boto3Integration.origin,
                SPANDATA.RPC_METHOD: "%s/%s"
                % (call_context.service_id, call_context.operation_name),
            },
        )

    span = sentry_sdk.start_span(
        name=description,
        op=OP.HTTP_CLIENT,
        origin=Boto3Integration.origin,
    )
    span.set_tag("aws.service_id", call_context.service_id.hyphenize())
    span.set_tag("aws.operation_name", call_context.operation_name)
    return span


def _finish_client_span(
    span: "Union[Span, StreamedSpan]",
    parsed: "Dict[str, Any]",
) -> None:
    span.__exit__(None, None, None)

    body = parsed.get("Body")
    if not isinstance(body, StreamingBody):
        return

    streaming_span: "Union[Span, StreamedSpan]"
    if isinstance(span, StreamedSpan):
        streaming_span = sentry_sdk.traces.start_span(
            name=span.name,
            parent_span=span,
            attributes={
                SPANDATA.SENTRY_OP: OP.HTTP_CLIENT_STREAM,
                SPANDATA.SENTRY_ORIGIN: Boto3Integration.origin,
            },
        )
    else:
        streaming_span = span.start_child(
            op=OP.HTTP_CLIENT_STREAM,
            name=span.description,
            origin=Boto3Integration.origin,
        )

    orig_read = body.read
    orig_close = body.close

    def sentry_streaming_body_read(*args: "Any", **kwargs: "Any") -> bytes:
        try:
            ret = orig_read(*args, **kwargs)
            if ret:
                return ret

            if isinstance(streaming_span, StreamedSpan):
                streaming_span.end()
            else:
                streaming_span.finish()
            return ret
        except Exception:
            if isinstance(streaming_span, StreamedSpan):
                streaming_span.end()
            else:
                streaming_span.finish()
            raise

    body.read = sentry_streaming_body_read  # type: ignore

    def sentry_streaming_body_close(*args: "Any", **kwargs: "Any") -> None:
        if isinstance(streaming_span, StreamedSpan):
            streaming_span.end()
        else:
            streaming_span.finish()
        orig_close(*args, **kwargs)

    body.close = sentry_streaming_body_close  # type: ignore


def _finish_client_span_with_error(
    span: "Union[Span, StreamedSpan]",
    exception: "BaseException",
) -> None:
    span.__exit__(type(exception), exception, exception.__traceback__)


def _set_request_attributes(
    span: "Union[Span, StreamedSpan]",
    request: "AWSRequest",
) -> None:
    client = sentry_sdk.get_client()

    parsed_url = None
    if request.url is not None:
        with capture_internal_exceptions():
            parsed_url = parse_url(request.url, sanitize=False)

    if isinstance(span, StreamedSpan):
        span.set_attributes(get_url_attributes(client, parsed_url))

        if request.method is not None:
            span.set_attribute(SPANDATA.HTTP_REQUEST_METHOD, request.method)

        return

    if parsed_url is not None:
        span.set_data("aws.request.url", parsed_url.url)
        span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
        span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

    if request.method is not None:
        span.set_data(SPANDATA.HTTP_METHOD, request.method)


def _add_request_breadcrumb(request: "AWSRequest") -> None:
    client = sentry_sdk.get_client()

    parsed_url = None
    if request.url is not None:
        with capture_internal_exceptions():
            parsed_url = parse_url(request.url, sanitize=False)

    breadcrumb: "dict[str, Any]" = {}

    if has_span_streaming_enabled(client.options):
        breadcrumb.update(get_url_attributes(client, parsed_url))
        if request.method is not None:
            breadcrumb[SPANDATA.HTTP_REQUEST_METHOD] = request.method
    else:
        if parsed_url is not None:
            breadcrumb.update(
                {
                    "aws.request.url": parsed_url.url,
                    SPANDATA.HTTP_QUERY: parsed_url.query,
                    SPANDATA.HTTP_FRAGMENT: parsed_url.fragment,
                }
            )

        if request.method is not None:
            breadcrumb[SPANDATA.HTTP_METHOD] = request.method

    add_http_breadcrumb(None, breadcrumb)


def _sentry_request_created(
    request: "AWSRequest", operation_name: str, **kwargs: "Any"
) -> None:
    """
    Enrich a single `AWSRequest` attempt. Botocore creates a fresh `AWSRequest` on every retry.
    https://github.com/boto/botocore/blob/develop/botocore/endpoint.py#L178-L202
    """
    client = sentry_sdk.get_client()
    if client.get_integration(Boto3Integration) is None:
        return

    with capture_internal_exceptions():
        _add_request_breadcrumb(request)

        if has_span_streaming_enabled(client.options):
            span = sentry_sdk.traces.get_current_span()
        else:
            span = sentry_sdk.get_current_span()
        if span is None:
            return

        _set_request_attributes(span, request)
        # each attempt has a fresh `request.context`; carry the active client span.
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
