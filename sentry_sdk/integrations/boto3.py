from functools import partial

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import _check_minimum_version, Integration, DidNotEnable
from sentry_sdk.tracing import Span
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    capture_internal_exceptions,
    parse_url,
    parse_version,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import Optional
    from typing import Type
    from typing import Union

    from botocore.model import ServiceId

try:
    from botocore import __version__ as BOTOCORE_VERSION
    from botocore.client import BaseClient
    from botocore.response import StreamingBody
    from botocore.awsrequest import AWSRequest
except ImportError:
    raise DidNotEnable("botocore is not installed")


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

    is_span_streaming_enabled = has_span_streaming_enabled(client.options)
    span: "Union[Span, StreamedSpan]"
    if is_span_streaming_enabled:
        span = sentry_sdk.traces.start_span(
            name=description,
            attributes={
                "sentry.op": OP.HTTP_CLIENT,
                "sentry.origin": Boto3Integration.origin,
                SPANDATA.RPC_METHOD: f"{service_id}/{operation_name}",
            },
        )
        if request.url is not None:
            with capture_internal_exceptions():
                parsed_url = parse_url(request.url, sanitize=False)
                span.set_attribute(SPANDATA.URL_FULL, parsed_url.url)
                span.set_attribute(SPANDATA.URL_QUERY, parsed_url.query)
                span.set_attribute(SPANDATA.URL_FRAGMENT, parsed_url.fragment)

        if request.method is not None:
            span.set_attribute(SPANDATA.HTTP_REQUEST_METHOD, request.method)
    else:
        span = sentry_sdk.start_span(
            op=OP.HTTP_CLIENT,
            name=description,
            origin=Boto3Integration.origin,
        )

        if request.url is not None:
            with capture_internal_exceptions():
                parsed_url = parse_url(request.url, sanitize=False)
                span.set_data("aws.request.url", parsed_url.url)
                span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
                span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

        span.set_tag("aws.service_id", service_id.hyphenize())
        span.set_tag("aws.operation_name", operation_name)
        span.set_data(SPANDATA.HTTP_METHOD, request.method)

    # We do it in order for subsequent http calls/retries be
    # attached to this span.
    span.__enter__()

    # request.context is an open-ended data-structure
    # where we can add anything useful in request life cycle.
    request.context["_sentrysdk_span"] = span


def _sentry_after_call(
    context: "Dict[str, Any]", parsed: "Dict[str, Any]", **kwargs: "Any"
) -> None:
    span: "Optional[Union[Span, StreamedSpan]]" = context.pop("_sentrysdk_span", None)

    # Span could be absent if the integration is disabled.
    if span is None:
        return
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
                "sentry.op": OP.HTTP_CLIENT_STREAM,
                "sentry.origin": Boto3Integration.origin,
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


def _sentry_after_call_error(
    context: "Dict[str, Any]", exception: "Type[BaseException]", **kwargs: "Any"
) -> None:
    span: "Optional[Union[Span, StreamedSpan]]" = context.pop("_sentrysdk_span", None)

    # Span could be absent if the integration is disabled.
    if span is None:
        return
    span.__exit__(type(exception), exception, None)
