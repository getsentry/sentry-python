import io
import json
import os
import subprocess
import sys
import platform

try:
    # py3
    from urllib.parse import parse_qsl
except ImportError:
    # py2
    from urlparse import parse_qsl  # type: ignore

try:
    # py3
    from json import JSONDecodeError
except ImportError:
    # py2 doesn't throw a specialized json error, just Value/TypeErrors
    JSONDecodeError = ValueError  # type: ignore

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.tracing_utils import EnvironHeaders, should_propagate_trace
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    SentryGraphQLClientError,
    capture_internal_exceptions,
    event_from_exception,
    logger,
    safe_repr,
    parse_url,
    _get_graphql_operation_name,
    _get_graphql_operation_type,
)
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Tuple

    from sentry_sdk._types import Event, EventProcessor, Hint


try:
    from httplib import HTTPConnection  # type: ignore
except ImportError:
    from http.client import HTTPConnection


_RUNTIME_CONTEXT = {
    "name": platform.python_implementation(),
    "version": "%s.%s.%s" % (sys.version_info[:3]),
    "build": sys.version,
}


class StdlibIntegration(Integration):
    identifier = "stdlib"

    def __init__(self, capture_graphql_errors=True):
        # type: (bool) -> None
        self.capture_graphql_errors = capture_graphql_errors

    @staticmethod
    def setup_once():
        # type: () -> None
        _install_httplib()
        _install_subprocess()

        @add_global_event_processor
        def add_python_runtime_context(event, hint):
            # type: (Event, Hint) -> Optional[Event]
            if Hub.current.get_integration(StdlibIntegration) is not None:
                contexts = event.setdefault("contexts", {})
                if isinstance(contexts, dict) and "runtime" not in contexts:
                    contexts["runtime"] = _RUNTIME_CONTEXT

            return event


def _install_httplib():
    # type: () -> None
    real_putrequest = HTTPConnection.putrequest
    real_endheaders = HTTPConnection.endheaders
    real_getresponse = HTTPConnection.getresponse

    def putrequest(self, method, url, *args, **kwargs):
        # type: (HTTPConnection, str, str, *Any, **Any) -> Any
        hub = Hub.current
        if hub.get_integration(StdlibIntegration) is None:
            return real_putrequest(self, method, url, *args, **kwargs)

        host = self.host
        port = self.port
        default_port = self.default_port

        real_url = url
        if real_url is None or not real_url.startswith(("http://", "https://")):
            real_url = "%s://%s%s%s" % (
                default_port == 443 and "https" or "http",
                host,
                port != default_port and ":%s" % port or "",
                url,
            )
        self._sentrysdk_url = real_url

        parsed_url = None
        with capture_internal_exceptions():
            parsed_url = parse_url(real_url, sanitize=False)
            self._sentrysdk_is_graphql_request = parsed_url.url.endswith("/graphql")

        span = hub.start_span(
            op=OP.HTTP_CLIENT,
            description="%s %s"
            % (method, parsed_url.url if parsed_url else SENSITIVE_DATA_SUBSTITUTE),
        )

        span.set_data(SPANDATA.HTTP_METHOD, method)
        if parsed_url is not None:
            span.set_data("url", parsed_url.url)
            span.set_data(SPANDATA.HTTP_QUERY, parsed_url.query)
            span.set_data(SPANDATA.HTTP_FRAGMENT, parsed_url.fragment)

        rv = real_putrequest(self, method, url, *args, **kwargs)

        if should_propagate_trace(hub, real_url):
            for key, value in hub.iter_trace_propagation_headers(span):
                logger.debug(
                    "[Tracing] Adding `{key}` header {value} to outgoing request to {real_url}.".format(
                        key=key, value=value, real_url=real_url
                    )
                )
                self.putheader(key, value)

        self._sentrysdk_span = span
        self._sentrysdk_method = method

        return rv

    def endheaders(self, message_body=None, **kwargs):
        # type: (HTTPConnection, Any, **Any) -> Any
        rv = real_endheaders(self, message_body, **kwargs)

        integration = Hub.current.get_integration(StdlibIntegration)
        if integration is None:
            return rv

        if integration.capture_graphql_errors and getattr(
            self, "_sentrysdk_is_graphql_request", False
        ):
            self._sentry_request_body = message_body

        return rv

    def getresponse(self, *args, **kwargs):
        # type: (HTTPConnection, *Any, **Any) -> Any
        rv = real_getresponse(self, *args, **kwargs)

        hub = Hub.current
        integration = hub.get_integration(StdlibIntegration)
        if integration is None:
            return rv

        span = getattr(self, "_sentrysdk_span", None)
        if span is not None:
            span.set_http_status(int(rv.status))
            span.set_data("reason", rv.reason)
            span.finish()

        url = getattr(self, "_sentrysdk_url", None)  # type: Optional[str]
        if url is None:
            return rv

        if integration.capture_graphql_errors:
            response_body = None
            if getattr(self, "_sentrysdk_is_graphql_request", False):
                with capture_internal_exceptions():
                    response_data = rv.read()
                    # once we've read() the body it can't be read() again by the
                    # app; save it so that it can be accessed again
                    saved_response = io.BytesIO(response_data)
                    rv.read = saved_response.read
                    rv.fp = saved_response
                    try:
                        # py3.6+ json.loads() can deal with bytes out of the box, but
                        # for older version we have to explicitly decode first
                        response_body = json.loads(response_data.decode())
                    except (JSONDecodeError, UnicodeDecodeError, TypeError):
                        return rv

            is_graphql_response_with_errors = isinstance(
                response_body, dict
            ) and response_body.get("errors")
            if is_graphql_response_with_errors:
                method = getattr(self, "_sentrysdk_method", None)  # type: Optional[str]
                request_body = getattr(self, "_sentry_request_body", None)
                with hub.configure_scope() as scope:
                    scope.add_event_processor(
                        _make_request_processor(
                            url, method, rv.status, request_body, response_body
                        )
                    )
                    try:
                        raise SentryGraphQLClientError
                    except SentryGraphQLClientError as ex:
                        event, hint = event_from_exception(
                            ex,
                            client_options=hub.client.options if hub.client else None,
                            mechanism={
                                "type": StdlibIntegration.identifier,
                                "handled": False,
                            },
                        )

                hub.capture_event(event, hint=hint)

        return rv

    HTTPConnection.putrequest = putrequest
    HTTPConnection.endheaders = endheaders
    HTTPConnection.getresponse = getresponse


def _make_request_processor(url, method, status, request_body, response_body):
    # type: (str, Optional[str], int, Any, Any) -> EventProcessor
    def stdlib_processor(
        event,  # type: Dict[str, Any]
        hint,  # type: Dict[str, Tuple[type, BaseException, Any]]
    ):
        # type: (...) -> Optional[Event]
        with capture_internal_exceptions():
            request_info = event.setdefault("request", {})

            parsed_url = parse_url(url, sanitize=False)

            if _should_send_default_pii():
                request_info["query_string"] = parsed_url.query

            request_info["url"] = parsed_url.url
            request_info["method"] = method

            if _should_send_default_pii():
                try:
                    request_info["data"] = json.loads(request_body.decode())
                except (JSONDecodeError, AttributeError):
                    pass

                if response_body:
                    contexts = event.setdefault("contexts", {})
                    response_context = contexts.setdefault("response", {})
                    response_context["data"] = response_body

            if parsed_url.url.endswith("/graphql"):
                request_info["api_target"] = "graphql"
                query = request_info.get("data")
                if method == "GET":
                    query = dict(parse_qsl(parsed_url.query))

                if query:
                    operation_name = _get_graphql_operation_name(query)
                    operation_type = _get_graphql_operation_type(query)
                    event["fingerprint"] = [operation_name, operation_type, status]
                    event["exception"]["values"][0][
                        "value"
                    ] = "GraphQL request failed, name: {}, type: {}".format(
                        operation_name, operation_type
                    )

        return event

    return stdlib_processor


def _init_argument(args, kwargs, name, position, setdefault_callback=None):
    # type: (List[Any], Dict[Any, Any], str, int, Optional[Callable[[Any], Any]]) -> Any
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


def _install_subprocess():
    # type: () -> None
    old_popen_init = subprocess.Popen.__init__

    def sentry_patched_popen_init(self, *a, **kw):
        # type: (subprocess.Popen[Any], *Any, **Any) -> None

        hub = Hub.current
        if hub.get_integration(StdlibIntegration) is None:
            return old_popen_init(self, *a, **kw)

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

        with hub.start_span(op=OP.SUBPROCESS, description=description) as span:
            for k, v in hub.iter_trace_propagation_headers(span):
                if env is None:
                    env = _init_argument(
                        a, kw, "env", 10, lambda x: dict(x or os.environ)
                    )
                env["SUBPROCESS_" + k.upper().replace("-", "_")] = v

            if cwd:
                span.set_data("subprocess.cwd", cwd)

            rv = old_popen_init(self, *a, **kw)

            span.set_tag("subprocess.pid", self.pid)
            return rv

    subprocess.Popen.__init__ = sentry_patched_popen_init  # type: ignore

    old_popen_wait = subprocess.Popen.wait

    def sentry_patched_popen_wait(self, *a, **kw):
        # type: (subprocess.Popen[Any], *Any, **Any) -> Any
        hub = Hub.current

        if hub.get_integration(StdlibIntegration) is None:
            return old_popen_wait(self, *a, **kw)

        with hub.start_span(op=OP.SUBPROCESS_WAIT) as span:
            span.set_tag("subprocess.pid", self.pid)
            return old_popen_wait(self, *a, **kw)

    subprocess.Popen.wait = sentry_patched_popen_wait  # type: ignore

    old_popen_communicate = subprocess.Popen.communicate

    def sentry_patched_popen_communicate(self, *a, **kw):
        # type: (subprocess.Popen[Any], *Any, **Any) -> Any
        hub = Hub.current

        if hub.get_integration(StdlibIntegration) is None:
            return old_popen_communicate(self, *a, **kw)

        with hub.start_span(op=OP.SUBPROCESS_COMMUNICATE) as span:
            span.set_tag("subprocess.pid", self.pid)
            return old_popen_communicate(self, *a, **kw)

    subprocess.Popen.communicate = sentry_patched_popen_communicate  # type: ignore


def get_subprocess_traceparent_headers():
    # type: () -> EnvironHeaders
    return EnvironHeaders(os.environ, prefix="SUBPROCESS_")
