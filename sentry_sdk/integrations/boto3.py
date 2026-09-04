from typing import TYPE_CHECKING
from urllib.parse import urlsplit

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
    from typing import Any, Dict, Mapping, Optional, Union

    from botocore.model import ServiceId


try:
    from botocore import __version__ as BOTOCORE_VERSION
    from botocore.awsrequest import AWSRequest
    from botocore.client import BaseClient
    from botocore.exceptions import ClientError
    from botocore.response import StreamingBody
except ImportError:
    raise DidNotEnable("botocore is not installed")

_AWS_RPC_SYSTEM_NAME = "aws-api"


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
        # botocore's internal identifier, e.g. `apigateway`.
        self.service_name: str = service_model.service_name
        # modeled AWS service identity used in span names, e.g. `API Gateway`.
        self.service_id: "ServiceId" = service_model.service_id
        self.operation_name: str = operation_name
        self.region_name: "Optional[str]" = getattr(client_meta, "region_name", None)
        self.endpoint_url: "Optional[str]" = getattr(client_meta, "endpoint_url", None)
        self.api_version: str = service_model.api_version
        # params may contain streams or arbitrary SDK objects; make a shallow copy
        # so nested values preserve original identity.
        self.api_params: "Dict[str, Any]" = (
            dict(api_params) if isinstance(api_params, dict) else {}
        )


def _set_span_attributes(
    span: "Union[Span, StreamedSpan]", attributes: "Dict[str, Any]"
) -> None:
    # streamed and legacy spans expose different attribute APIs.
    if isinstance(span, StreamedSpan):
        span.set_attributes(attributes)
        return

    for key, value in attributes.items():
        span.set_data(key, value)


def _get_server_attributes(endpoint_url: "Optional[str]") -> "Dict[str, Any]":
    if not endpoint_url:
        return {}

    default_ports = {
        "http": 80,
        "https": 443,
    }

    try:
        parsed_url = urlsplit(endpoint_url)
        if parsed_url.scheme not in default_ports or not parsed_url.hostname:
            return {}

        # `server.port` is only defined together with `server.address`. Infer the
        # effective port when the configured HTTP(S) endpoint omits it.
        # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/
        return {
            SPANDATA.SERVER_ADDRESS: parsed_url.hostname,
            SPANDATA.SERVER_PORT: parsed_url.port or default_ports[parsed_url.scheme],
        }

    except (TypeError, UnicodeError, ValueError):
        # Invalid client metadata must not prevent the AWS call from running.
        return {}


def _get_client_span_attributes(
    call_context: "_ClientCallContext",
) -> "Dict[str, Any]":
    # AWS keeps service and operation separate, so `rpc.method` is only the
    # operation. https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
    attributes = {
        SPANDATA.RPC_METHOD: call_context.operation_name,
        SPANDATA.RPC_SYSTEM_NAME: _AWS_RPC_SYSTEM_NAME,
    }

    if call_context.region_name:
        attributes[SPANDATA.CLOUD_REGION] = call_context.region_name

    attributes.update(_get_server_attributes(call_context.endpoint_url))
    return attributes


def _get_response_span_attributes(
    parsed: "Mapping[str, Any]",
) -> "Dict[str, Any]":
    metadata = parsed.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return {}

    attributes = {}

    status_code = metadata.get("HTTPStatusCode")
    if status_code is not None:
        attributes[SPANDATA.HTTP_STATUS_CODE] = status_code

    headers = metadata.get("HTTPHeaders")
    if not isinstance(headers, dict):
        headers = {}

    request_id = metadata.get("RequestId")
    if request_id is None:
        request_id = (
            headers.get("x-amzn-requestid")
            or headers.get("x-amzn-request-id")
            or headers.get("x-amz-request-id")
        )
    if request_id is not None:
        attributes[SPANDATA.AWS_REQUEST_ID] = request_id

    # S3's `HostId` is the extended request ID returned in `x-amz-id-2`.
    # https://docs.aws.amazon.com/AmazonS3/latest/developerguide/get-request-ids.html
    extended_request_id = metadata.get("HostId") or headers.get("x-amz-id-2")
    if extended_request_id is not None:
        attributes[SPANDATA.AWS_EXTENDED_REQUEST_ID] = extended_request_id

    return attributes


def _get_error_type(exception: "BaseException") -> str:
    if isinstance(exception, ClientError):
        # botocore wraps all AWS service errors in `ClientError`; `Error.Code`
        # identifies actual service-specific error, e.g. `AccessDenied`.
        # https://docs.aws.amazon.com/boto3/latest/guide/error-handling.html
        error = exception.response.get("Error")
        if isinstance(error, dict) and error.get("Code") is not None:
            return str(error["Code"])

    # failures before a service response, have no error code. Use exception type
    # instead. https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/
    exception_type = type(exception)
    exception_name = exception_type.__qualname__
    exception_module = exception_type.__module__
    if exception_module not in ("builtins", "__builtins__"):
        return "%s.%s" % (exception_module, exception_name)
    return exception_name


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

    # AWS client spans use `Service.Operation`, e.g. `DynamoDB.GetItem`.
    # https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
    span_name = "%s.%s" % (call_context.service_id, call_context.operation_name)
    attributes = _get_client_span_attributes(call_context)

    if has_span_streaming_enabled(client.options):
        if sentry_sdk.traces.get_current_span() is None:
            return None

        # `start_span()` evaluates `ignore_spans` against the initial attributes.
        # https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/#rpc-client-span
        attributes.update(
            {
                SPANDATA.SENTRY_OP: OP.HTTP_CLIENT,
                SPANDATA.SENTRY_ORIGIN: Boto3Integration.origin,
            }
        )
        return sentry_sdk.traces.start_span(
            name=span_name,
            attributes=attributes,
        )

    span = sentry_sdk.start_span(
        name=span_name,
        op=OP.HTTP_CLIENT,
        origin=Boto3Integration.origin,
    )
    _set_span_attributes(span, attributes)
    span.set_tag("aws.service_id", call_context.service_id.hyphenize())
    span.set_tag("aws.operation_name", call_context.operation_name)
    return span


def _finish_client_span(
    span: "Union[Span, StreamedSpan]",
    parsed: "Dict[str, Any]",
) -> None:
    # response metadata is only available after the call. Add before `__exit__()`.
    _set_span_attributes(span, _get_response_span_attributes(parsed))
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
        except Exception as exc:
            # enrichment must not replace exception raised by `orig_read()`.
            # finish span with error, then re-raise.
            with capture_internal_exceptions():
                _set_span_attributes(
                    streaming_span,
                    {SPANDATA.ERROR_TYPE: _get_error_type(exc)},
                )
                streaming_span.__exit__(type(exc), exc, exc.__traceback__)
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
    if isinstance(exception, ClientError):
        _set_span_attributes(span, _get_response_span_attributes(exception.response))

    # `error.type` is required when RPC operation fails.
    _set_span_attributes(span, {SPANDATA.ERROR_TYPE: _get_error_type(exception)})
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
    Enrich a single `AWSRequest` attempt. Botocore creates a
    fresh `AWSRequest` on every retry.
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
            add_sentry_baggage_to_headers(combined_baggage, header_value)
            _replace_header(
                request, BAGGAGE_HEADER_NAME, combined_baggage[BAGGAGE_HEADER_NAME]
            )
