import os
import platform
import subprocess
import sys
from http.client import HTTPConnection, HTTPResponse
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor, should_send_default_pii
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME, SENTRY_TRACE_HEADER_NAME, Span
from sentry_sdk.tracing_utils import (
    EnvironHeaders,
    add_http_breadcrumb,
    add_http_request_source,
    has_span_streaming_enabled,
    should_propagate_trace,
)
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    _get_aws_sigv4_signed_headers_from_authorization_header,
    _get_aws_sigv4_signed_headers_from_url_query_string,
    capture_internal_exceptions,
    ensure_integration_enabled,
    is_sentry_url,
    logger,
    parse_url,
    safe_repr,
)

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Set, Union

    from sentry_sdk._types import Event, Hint


_RUNTIME_CONTEXT: "dict[str, object]" = {
    "name": platform.python_implementation(),
    "version": "%s.%s.%s" % (sys.version_info[:3]),
    "build": sys.version,
}

_SENTRY_HEADER_NAMES = frozenset((BAGGAGE_HEADER_NAME, SENTRY_TRACE_HEADER_NAME))

try:
    from botocore.awsrequest import AWSHTTPConnection, AWSHTTPSConnection
except ImportError:
    AWSHTTPConnection = None  # type: ignore[misc,assignment]
    AWSHTTPSConnection = None  # type: ignore[misc,assignment]


class StdlibIntegration(Integration):
    identifier = "stdlib"

    @staticmethod
    def setup_once() -> None:
        _install_httplib()
        _install_subprocess()

        @add_global_event_processor
        def add_python_runtime_context(
            event: "Event", hint: "Hint"
        ) -> "Optional[Event]":
            client = sentry_sdk.get_client()
            if client.get_integration(StdlibIntegration) is not None:
                contexts = event.setdefault("contexts", {})
                if isinstance(contexts, dict) and "runtime" not in contexts:
                    contexts["runtime"] = _RUNTIME_CONTEXT

            return event


def _complete_span(span: "Union[Span, StreamedSpan]") -> None:
    if isinstance(span, StreamedSpan):
        with capture_internal_exceptions():
            add_http_request_source(span)
        span.end()
    else:
        span.finish()
        with capture_internal_exceptions():
            add_http_request_source(span)


def _get_wrapped_putheader(
    original_putheader: "Callable[..., Any]",
) -> "Callable[..., Any]":
    """Record existing `sentry-trace` and SigV4-signed propagation headers to skip."""

    def putheader(self: "HTTPConnection", header: "Any", *values: "Any") -> "Any":
        rv = original_putheader(self, header, *values)

        headers_to_skip: "Optional[Set[str]]" = getattr(
            self, "_sentrysdk_headers_to_skip", None
        )
        if headers_to_skip is None:
            return rv

        if isinstance(header, bytes):
            normalized_header = header.decode("ascii", "ignore").lower()
        elif isinstance(header, str):
            normalized_header = header.lower()
        else:
            return rv

        # existing `sentry-trace`: skip so it is not duplicated.
        if normalized_header == SENTRY_TRACE_HEADER_NAME:
            headers_to_skip.add(normalized_header)

        if normalized_header == "authorization" and values:
            with capture_internal_exceptions():
                authorization = values[0]
                if isinstance(authorization, bytes):
                    authorization = authorization.decode("latin-1")
                # `SignedHeaders` lists fields covered by SigV4.
                signed_headers = (
                    _get_aws_sigv4_signed_headers_from_authorization_header(
                        authorization
                    )
                )
                # skip signed `sentry-trace` and `baggage`.
                headers_to_skip.update(
                    _SENTRY_HEADER_NAMES.intersection(signed_headers)
                )

        return rv

    return putheader


def _get_wrapped_endheaders(
    original_endheaders: "Callable[..., Any]",
) -> "Callable[..., Any]":
    """
    Add unsigned propagation headers immediately before sending.

    `sentry-trace`: add if missing; leave an existing or signed value as-is.
    `baggage`: add if missing; append if unsigned since multiple fields are
    allowed and combined (https://www.w3.org/TR/baggage/#baggage-http-header-format);
    leave a signed value as-is since it is part of the SigV4 signature.
    """

    def endheaders(self: "HTTPConnection", *args: "Any", **kwargs: "Any") -> "Any":
        real_url = getattr(self, "_sentrysdk_real_url", None)
        span = getattr(self, "_sentrysdk_span", None)

        try:
            if real_url is not None:
                with capture_internal_exceptions():
                    headers_to_skip = getattr(self, "_sentrysdk_headers_to_skip", None)

                    if headers_to_skip is not None:
                        # presigned URLs list signed names in the query string.
                        query_signed_headers = (
                            _get_aws_sigv4_signed_headers_from_url_query_string(
                                real_url
                            )
                        )
                        headers_to_skip.update(
                            _SENTRY_HEADER_NAMES.intersection(query_signed_headers)
                        )

                        for (
                            header_name,
                            header_value,
                        ) in sentry_sdk.get_current_scope().iter_trace_propagation_headers(
                            span=span
                        ):
                            # skip signed headers and an existing `sentry-trace`.
                            if header_name.lower() in headers_to_skip:
                                continue

                            logger.debug(
                                "[Tracing] Adding `{key}` header {value} to outgoing request to {real_url}.".format(
                                    key=header_name,
                                    value=header_value,
                                    real_url=real_url,
                                )
                            )
                            self.putheader(header_name, header_value)
            return original_endheaders(self, *args, **kwargs)
        finally:
            self._sentrysdk_real_url = None  # type: ignore[attr-defined]

    return endheaders


def _get_wrapped_putrequest(
    original_putrequest: "Callable[..., Any]",
) -> "Callable[..., Any]":
    """Start `headers_to_skip` before headers are written."""

    def putrequest(
        self: "HTTPConnection", method: str, url: str, *args: "Any", **kwargs: "Any"
    ) -> "Any":
        # existing `sentry-trace` and SigV4-signed propagation headers are skipped.
        self._sentrysdk_headers_to_skip = set()  # type: ignore[attr-defined]

        try:
            rv = original_putrequest(self, method, url, *args, **kwargs)
        except BaseException:
            # do not leave partial request state on a reusable connection when
            # the underlying request setup fails.
            self._sentrysdk_headers_to_skip = None  # type: ignore[attr-defined]
            raise

        return rv

    return putrequest


def _patch_aws_connection() -> None:
    """
    Patch AWS connection classes so trace propagation does not break SigV4.

    Botocore signs the request before headers reach HTTPConnection. Wait until
    `endheaders()` to add unsigned propagation headers, after `putheader()` has
    recorded existing `sentry-trace` and signed field names.
    """
    if AWSHTTPConnection is not None:
        AWSHTTPConnection.putheader = _get_wrapped_putheader(  # type: ignore[method-assign]
            AWSHTTPConnection.putheader
        )
        AWSHTTPConnection.endheaders = _get_wrapped_endheaders(  # type: ignore[method-assign]
            AWSHTTPConnection.endheaders
        )
        AWSHTTPConnection.putrequest = _get_wrapped_putrequest(  # type: ignore[method-assign]
            AWSHTTPConnection.putrequest
        )

    if AWSHTTPSConnection is not None:
        AWSHTTPSConnection.putheader = _get_wrapped_putheader(  # type: ignore[method-assign]
            AWSHTTPSConnection.putheader
        )
        AWSHTTPSConnection.endheaders = _get_wrapped_endheaders(  # type: ignore[method-assign]
            AWSHTTPSConnection.endheaders
        )
        AWSHTTPSConnection.putrequest = _get_wrapped_putrequest(  # type: ignore[method-assign]
            AWSHTTPSConnection.putrequest
        )


def _install_httplib() -> None:
    real_putrequest = HTTPConnection.putrequest
    real_getresponse = HTTPConnection.getresponse
    real_read = HTTPResponse.read
    real_close = HTTPResponse.close

    def putrequest(
        self: "HTTPConnection", method: str, url: str, *args: "Any", **kwargs: "Any"
    ) -> "Any":
        default_port = self.default_port

        # proxies go through set_tunnel
        tunnel_host = getattr(self, "_tunnel_host", None)
        if tunnel_host:
            host = tunnel_host
            port = getattr(self, "_tunnel_port", default_port)
        else:
            host = self.host
            port = self.port

        client = sentry_sdk.get_client()
        if client.get_integration(StdlibIntegration) is None or is_sentry_url(
            client, host
        ):
            return real_putrequest(self, method, url, *args, **kwargs)

        real_url = url
        if real_url is None or not real_url.startswith(("http://", "https://")):
            real_url = "%s://%s%s%s" % (
                default_port == 443 and "https" or "http",
                host,
                port != default_port and ":%s" % port or "",
                url,
            )

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(real_url, sanitize=False)

        span_streaming = has_span_streaming_enabled(client.options)
        span: "Union[Span, StreamedSpan, None]" = None
        breadcrumb: "dict[str, Any]" = {}

        if span_streaming:
            breadcrumb[SPANDATA.HTTP_REQUEST_METHOD] = method
            if parsed_url is not None and should_send_default_pii():
                breadcrumb.update(
                    {
                        SPANDATA.URL_FRAGMENT: parsed_url.fragment,
                        SPANDATA.URL_FULL: parsed_url.url,
                        SPANDATA.URL_QUERY: parsed_url.query,
                    }
                )

            if sentry_sdk.traces.get_current_span() is not None:
                span = sentry_sdk.traces.start_span(
                    name="%s %s"
                    % (
                        method,
                        parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE,
                    ),
                    attributes={
                        "sentry.origin": "auto.http.stdlib.httplib",
                        "sentry.op": OP.HTTP_CLIENT,
                        SPANDATA.HTTP_REQUEST_METHOD: method,
                    },
                )

                if parsed_url is not None and should_send_default_pii():
                    span.set_attribute(SPANDATA.URL_FRAGMENT, parsed_url.fragment)
                    span.set_attribute(SPANDATA.URL_FULL, parsed_url.url)
                    span.set_attribute(SPANDATA.URL_QUERY, parsed_url.query)

                set_on_span = span.set_attribute

        else:
            span = sentry_sdk.start_span(
                op=OP.HTTP_CLIENT,
                name="%s %s"
                % (method, parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE),
                origin="auto.http.stdlib.httplib",
            )

            span.set_data(SPANDATA.HTTP_METHOD, method)
            breadcrumb[SPANDATA.HTTP_METHOD] = method

            if parsed_url is not None:
                span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)
                span.set_data("url", parsed_url.url)
                span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)

                breadcrumb.update(
                    {
                        SPANDATA.HTTP_FRAGMENT: parsed_url.fragment,
                        "url": parsed_url.url,
                        SPANDATA.HTTP_QUERY: parsed_url.query,
                    }
                )

            set_on_span = span.set_data

        # for proxies, these point to the proxy host/port
        if tunnel_host:
            if span:
                set_on_span(SPANDATA.NETWORK_PEER_ADDRESS, self.host)
                set_on_span(SPANDATA.NETWORK_PEER_PORT, self.port)

            breadcrumb.update(
                {
                    SPANDATA.NETWORK_PEER_ADDRESS: self.host,
                    SPANDATA.NETWORK_PEER_PORT: self.port,
                }
            )

        rv = real_putrequest(self, method, url, *args, **kwargs)

        # if `_sentrysdk_headers_to_skip` is set, inject headers in `endheaders()`
        # after botocore has supplied the final headers.
        if should_propagate_trace(client, real_url) and not hasattr(
            self, "_sentrysdk_headers_to_skip"
        ):
            for (
                key,
                value,
            ) in sentry_sdk.get_current_scope().iter_trace_propagation_headers(
                span=span
            ):
                logger.debug(
                    "[Tracing] Adding `{key}` header {value} to outgoing request to {real_url}.".format(
                        key=key, value=value, real_url=real_url
                    )
                )
                self.putheader(key, value)
        elif should_propagate_trace(client, real_url):
            self._sentrysdk_real_url = real_url  # type: ignore[attr-defined]

        self._sentrysdk_span = span  # type: ignore[attr-defined]
        self._sentrysdk_breadcrumb = breadcrumb  # type: ignore[attr-defined]

        return rv

    def getresponse(self: "HTTPConnection", *args: "Any", **kwargs: "Any") -> "Any":
        span = getattr(self, "_sentrysdk_span", None)
        breadcrumb = getattr(self, "_sentrysdk_breadcrumb", None)

        try:
            rv = real_getresponse(self, *args, **kwargs)
        except BaseException as ex:
            if span:
                _complete_span(span)
            if (
                breadcrumb
                and "getresponse() got an unexpected keyword argument 'buffering'"
                not in str(ex)
            ):
                # the exception msg check is needed for Python 3.6/requests compat
                add_http_breadcrumb(None, breadcrumb)
            raise

        status_code = int(rv.status)

        if breadcrumb:
            breadcrumb[SPANDATA.HTTP_STATUS_CODE] = status_code

        if span is None:
            if breadcrumb:
                add_http_breadcrumb(status_code, breadcrumb)
            return rv

        if isinstance(span, StreamedSpan):
            span.status = "error" if status_code >= 400 else "ok"
            span.set_attribute(SPANDATA.HTTP_STATUS_CODE, status_code)
        elif isinstance(span, Span):
            span.set_http_status(status_code)
            span.set_data("reason", rv.reason)
            if breadcrumb:
                breadcrumb["reason"] = rv.reason

        # getresponse doesn't include actually reading the response body. This
        # is done in read(). So if the metadata/headers suggest there's a body to
        # read, don't finish the span just yet, but save it for ending it later.
        has_body = rv.chunked or (rv.length is not None and rv.length > 0)
        if has_body:
            rv._sentrysdk_span = span  # type: ignore[attr-defined]
        else:
            _complete_span(span)

        if breadcrumb:
            # Regardless of whether the response itself has been fully read or not,
            # the breadcrumb can now be emitted since we now have the status code.
            add_http_breadcrumb(status_code, breadcrumb)

        return rv

    def read(self: "HTTPResponse", *args: "Any", **kwargs: "Any") -> "Any":
        try:
            return real_read(self, *args, **kwargs)
        finally:
            span = getattr(self, "_sentrysdk_span", None)
            # read() might be called multiple times to consume a single body,
            # so we can't just end the span when read() is done. Instead,
            # try to figure out whether the response body has been fully read.
            if span and (self.fp is None or self.closed):
                self._sentrysdk_span = None  # type: ignore[attr-defined]
                _complete_span(span)

    def close(self: "HTTPResponse") -> None:
        # We patch close() as a best effort fallback in case the span is not
        # ended yet in getresponse() or read().

        try:
            real_close(self)
        finally:
            span = getattr(self, "_sentrysdk_span", None)
            if span is not None:
                self._sentrysdk_span = None  # type: ignore[attr-defined]
                _complete_span(span)

    _patch_aws_connection()
    HTTPConnection.putrequest = putrequest  # type: ignore[method-assign]
    HTTPConnection.getresponse = getresponse  # type: ignore[method-assign]
    HTTPResponse.read = read  # type: ignore[method-assign]
    HTTPResponse.close = close  # type: ignore[assignment,method-assign]


def _init_argument(
    args: "List[Any]",
    kwargs: "Dict[Any, Any]",
    name: str,
    position: int,
    setdefault_callback: "Optional[Callable[[Any], Any]]" = None,
) -> "Any":
    """
    given (*args, **kwargs) of a function call, retrieve (and optionally set a
    default for) an argument by either name or position.

    This is useful for wrapping functions with complex type signatures and
    extracting a few arguments without needing to redefine that function's
    entire type signature.
    """

    if name in kwargs:
        rv = kwargs[name]
        if setdefault_callback is not None:
            rv = setdefault_callback(rv)
        if rv is not None:
            kwargs[name] = rv
    elif position < len(args):
        rv = args[position]
        if setdefault_callback is not None:
            rv = setdefault_callback(rv)
        if rv is not None:
            args[position] = rv
    else:
        rv = setdefault_callback and setdefault_callback(None)
        if rv is not None:
            kwargs[name] = rv

    return rv


def _install_subprocess() -> None:
    old_popen_init = subprocess.Popen.__init__

    @ensure_integration_enabled(StdlibIntegration, old_popen_init)
    def sentry_patched_popen_init(
        self: "subprocess.Popen[Any]", *a: "Any", **kw: "Any"
    ) -> None:
        # Convert from tuple to list to be able to set values.
        a = list(a)

        args = _init_argument(a, kw, "args", 0) or []
        cwd = _init_argument(a, kw, "cwd", 9)

        # if args is not a list or tuple (and e.g. some iterator instead),
        # let's not use it at all. There are too many things that can go wrong
        # when trying to collect an iterator into a list and setting that list
        # into `a` again.
        #
        # Also invocations where `args` is not a sequence are not actually
        # legal. They just happen to work under CPython.
        description = None

        if isinstance(args, (list, tuple)) and len(args) < 100:
            with capture_internal_exceptions():
                description = " ".join(map(str, args))

        if description is None:
            description = safe_repr(args)

        env = None

        sentry_sdk.add_breadcrumb(
            type="subprocess",
            category="subprocess",
            message=description,
            data={"subprocess.cwd": cwd} if cwd else {},
        )

        span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
        span: "Union[Span, StreamedSpan]"
        if span_streaming:
            if sentry_sdk.traces.get_current_span() is None:
                return old_popen_init(self, *a, **kw)

            span = sentry_sdk.traces.start_span(
                name=description,
                attributes={
                    "sentry.op": OP.SUBPROCESS,
                    "sentry.origin": "auto.subprocess.stdlib.subprocess",
                },
            )
        else:
            span = sentry_sdk.start_span(
                op=OP.SUBPROCESS,
                name=description,
                origin="auto.subprocess.stdlib.subprocess",
            )

        with span:
            for k, v in sentry_sdk.get_current_scope().iter_trace_propagation_headers(
                span=span
            ):
                if env is None:
                    env = _init_argument(
                        a,
                        kw,
                        "env",
                        10,
                        lambda x: dict(x if x is not None else os.environ),
                    )
                env["SUBPROCESS_" + k.upper().replace("-", "_")] = v

            if cwd and isinstance(span, Span):
                span.set_data("subprocess.cwd", cwd)

            rv = old_popen_init(self, *a, **kw)

            if isinstance(span, StreamedSpan):
                span.set_attribute(SPANDATA.PROCESS_PID, self.pid)
            else:
                span.set_tag("subprocess.pid", self.pid)

            return rv

    subprocess.Popen.__init__ = sentry_patched_popen_init  # type: ignore

    old_popen_wait = subprocess.Popen.wait

    @ensure_integration_enabled(StdlibIntegration, old_popen_wait)
    def sentry_patched_popen_wait(
        self: "subprocess.Popen[Any]", *a: "Any", **kw: "Any"
    ) -> "Any":
        span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
        if span_streaming:
            if sentry_sdk.traces.get_current_span() is None:
                return old_popen_wait(self, *a, **kw)
            with sentry_sdk.traces.start_span(
                name=OP.SUBPROCESS_WAIT,
                attributes={
                    "sentry.op": OP.SUBPROCESS_WAIT,
                    "sentry.origin": "auto.subprocess.stdlib.subprocess",
                },
            ) as span:
                span.set_attribute(SPANDATA.PROCESS_PID, self.pid)
                return old_popen_wait(self, *a, **kw)
        else:
            with sentry_sdk.start_span(
                op=OP.SUBPROCESS_WAIT,
                origin="auto.subprocess.stdlib.subprocess",
            ) as span:
                span.set_tag("subprocess.pid", self.pid)
                return old_popen_wait(self, *a, **kw)

    subprocess.Popen.wait = sentry_patched_popen_wait  # type: ignore

    old_popen_communicate = subprocess.Popen.communicate

    @ensure_integration_enabled(StdlibIntegration, old_popen_communicate)
    def sentry_patched_popen_communicate(
        self: "subprocess.Popen[Any]", *a: "Any", **kw: "Any"
    ) -> "Any":
        span_streaming = has_span_streaming_enabled(sentry_sdk.get_client().options)
        if span_streaming:
            if sentry_sdk.traces.get_current_span() is None:
                return old_popen_communicate(self, *a, **kw)
            with sentry_sdk.traces.start_span(
                name=OP.SUBPROCESS_COMMUNICATE,
                attributes={
                    "sentry.op": OP.SUBPROCESS_COMMUNICATE,
                    "sentry.origin": "auto.subprocess.stdlib.subprocess",
                },
            ) as span:
                span.set_attribute(SPANDATA.PROCESS_PID, self.pid)
                return old_popen_communicate(self, *a, **kw)
        else:
            with sentry_sdk.start_span(
                op=OP.SUBPROCESS_COMMUNICATE,
                origin="auto.subprocess.stdlib.subprocess",
            ) as span:
                span.set_tag("subprocess.pid", self.pid)
                return old_popen_communicate(self, *a, **kw)

    subprocess.Popen.communicate = sentry_patched_popen_communicate  # type: ignore


def get_subprocess_traceparent_headers() -> "EnvironHeaders":
    return EnvironHeaders(os.environ, prefix="SUBPROCESS_")
