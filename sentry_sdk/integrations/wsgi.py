from __future__ import annotations
import sys
from functools import partial

import sentry_sdk
from sentry_sdk._werkzeug import get_host, _get_headers
from sentry_sdk.consts import OP
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.integrations._wsgi_common import (
    DEFAULT_HTTP_METHODS_TO_CAPTURE,
    _filter_headers,
    _request_headers_to_span_attributes,
)
from sentry_sdk.sessions import track_session
from sentry_sdk.tracing import Span, TransactionSource
from sentry_sdk.utils import (
    ContextVar,
    capture_internal_exceptions,
    event_from_exception,
    reraise,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable
    from typing import Dict
    from typing import Iterator
    from typing import Iterable
    from typing import Any
    from typing import Tuple
    from typing import List
    from typing import Optional
    from typing import Protocol

    from sentry_sdk.utils import ExcInfo
    from sentry_sdk._types import Event, EventProcessor

    WsgiResponseIter = Iterable[bytes]
    WsgiResponseHeaders = List[Tuple[str, str]]

    class StartResponse(Protocol):
        def __call__(
            self,
            status: str,
            response_headers: WsgiResponseHeaders,
            exc_info: Optional[ExcInfo] = None,
        ) -> WsgiResponseIter:
            pass


_wsgi_middleware_applied = ContextVar("sentry_wsgi_middleware_applied")

DEFAULT_TRANSACTION_NAME = "generic WSGI request"

ENVIRON_TO_ATTRIBUTE = {
    "PATH_INFO": "url.path",
    "QUERY_STRING": "url.query",
    "REQUEST_METHOD": "http.request.method",
    "SERVER_NAME": "server.address",
    "SERVER_PORT": "server.port",
    "wsgi.url_scheme": "url.scheme",
}


def wsgi_decoding_dance(s: str, charset: str = "utf-8", errors: str = "replace") -> str:
    return s.encode("latin1").decode(charset, errors)


def get_request_url(environ: Dict[str, str], use_x_forwarded_for: bool = False) -> str:
    """Return the absolute URL without query string for the given WSGI
    environment."""
    script_name = environ.get("SCRIPT_NAME", "").rstrip("/")
    path_info = environ.get("PATH_INFO", "").lstrip("/")
    path = f"{script_name}/{path_info}"

    return "%s://%s/%s" % (
        environ.get("wsgi.url_scheme"),
        get_host(environ, use_x_forwarded_for),
        wsgi_decoding_dance(path).lstrip("/"),
    )


class SentryWsgiMiddleware:
    __slots__ = (
        "app",
        "use_x_forwarded_for",
        "span_origin",
        "http_methods_to_capture",
    )

    def __init__(
        self,
        app: Callable[[Dict[str, str], Callable[..., Any]], Any],
        use_x_forwarded_for: bool = False,
        span_origin: Optional[str] = None,
        http_methods_to_capture: Tuple[str, ...] = DEFAULT_HTTP_METHODS_TO_CAPTURE,
    ) -> None:
        self.app = app
        self.use_x_forwarded_for = use_x_forwarded_for
        self.span_origin = span_origin
        self.http_methods_to_capture = http_methods_to_capture

    def __call__(
        self, environ: Dict[str, str], start_response: Callable[..., Any]
    ) -> _ScopedResponse:
        if _wsgi_middleware_applied.get(False):
            return self.app(environ, start_response)

        _wsgi_middleware_applied.set(True)
        try:
            with sentry_sdk.isolation_scope() as scope:
                scope.set_transaction_name(
                    DEFAULT_TRANSACTION_NAME, source=TransactionSource.ROUTE
                )

                with track_session(scope, session_mode="request"):
                    with capture_internal_exceptions():
                        scope.clear_breadcrumbs()
                        scope._name = "wsgi"
                        scope.add_event_processor(
                            _make_wsgi_event_processor(
                                environ, self.use_x_forwarded_for
                            )
                        )
                    method = environ.get("REQUEST_METHOD", "").upper()
                    should_trace = method in self.http_methods_to_capture
                    if should_trace:
                        with sentry_sdk.continue_trace(environ):
                            with sentry_sdk.start_span(
                                op=OP.HTTP_SERVER,
                                name=DEFAULT_TRANSACTION_NAME,
                                source=TransactionSource.ROUTE,
                                origin=self.span_origin,
                                attributes=_prepopulate_attributes(
                                    environ, self.use_x_forwarded_for
                                ),
                            ) as span:
                                response = self._run_original_app(
                                    environ, start_response, span
                                )
                    else:
                        response = self._run_original_app(environ, start_response, None)

        finally:
            _wsgi_middleware_applied.set(False)

        return _ScopedResponse(scope, response)

    def _run_original_app(
        self,
        environ: dict[str, str],
        start_response: StartResponse,
        span: Optional[Span],
    ) -> Any:
        try:
            return self.app(
                environ,
                partial(
                    _sentry_start_response,
                    start_response,
                    span,
                ),
            )
        except BaseException:
            reraise(*_capture_exception())


def _sentry_start_response(
    old_start_response: StartResponse,
    span: Optional[Span],
    status: str,
    response_headers: WsgiResponseHeaders,
    exc_info: Optional[ExcInfo] = None,
) -> WsgiResponseIter:
    with capture_internal_exceptions():
        status_int = int(status.split(" ", 1)[0])
        if span is not None:
            span.set_http_status(status_int)

    if exc_info is None:
        # The Django Rest Framework WSGI test client, and likely other
        # (incorrect) implementations, cannot deal with the exc_info argument
        # if one is present. Avoid providing a third argument if not necessary.
        return old_start_response(status, response_headers)
    else:
        return old_start_response(status, response_headers, exc_info)


def _get_environ(environ: Dict[str, str]) -> Iterator[Tuple[str, str]]:
    """
    Returns our explicitly included environment variables we want to
    capture (server name, port and remote addr if pii is enabled).
    """
    keys = ["SERVER_NAME", "SERVER_PORT"]
    if should_send_default_pii():
        # make debugging of proxy setup easier. Proxy headers are
        # in headers.
        keys += ["REMOTE_ADDR"]

    for key in keys:
        if key in environ:
            yield key, environ[key]


def get_client_ip(environ: Dict[str, str]) -> Optional[Any]:
    """
    Infer the user IP address from various headers. This cannot be used in
    security sensitive situations since the value may be forged from a client,
    but it's good enough for the event payload.
    """
    try:
        return environ["HTTP_X_FORWARDED_FOR"].split(",")[0].strip()
    except (KeyError, IndexError):
        pass

    try:
        return environ["HTTP_X_REAL_IP"]
    except KeyError:
        pass

    return environ.get("REMOTE_ADDR")


def _capture_exception() -> ExcInfo:
    """
    Captures the current exception and sends it to Sentry.
    Returns the ExcInfo tuple to it can be reraised afterwards.
    """
    exc_info = sys.exc_info()
    e = exc_info[1]

    # SystemExit(0) is the only uncaught exception that is expected behavior
    should_skip_capture = isinstance(e, SystemExit) and e.code in (0, None)
    if not should_skip_capture:
        event, hint = event_from_exception(
            exc_info,
            client_options=sentry_sdk.get_client().options,
            mechanism={"type": "wsgi", "handled": False},
        )
        sentry_sdk.capture_event(event, hint=hint)

    return exc_info


class _ScopedResponse:
    """
    Users a separate scope for each response chunk.

    This will make WSGI apps more tolerant against:
    - WSGI servers streaming responses from a different thread/from
      different threads than the one that called start_response
    - close() not being called
    - WSGI servers streaming responses interleaved from the same thread
    """

    __slots__ = ("_response", "_scope")

    def __init__(self, scope: sentry_sdk.Scope, response: Iterator[bytes]) -> None:
        self._scope = scope
        self._response = response

    def __iter__(self) -> Iterator[bytes]:
        iterator = iter(self._response)

        while True:
            with sentry_sdk.use_isolation_scope(self._scope):
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                except BaseException:
                    reraise(*_capture_exception())

            yield chunk

    def close(self) -> None:
        with sentry_sdk.use_isolation_scope(self._scope):
            try:
                self._response.close()  # type: ignore
            except AttributeError:
                pass
            except BaseException:
                reraise(*_capture_exception())


def _make_wsgi_event_processor(
    environ: Dict[str, str], use_x_forwarded_for: bool
) -> EventProcessor:
    # It's a bit unfortunate that we have to extract and parse the request data
    # from the environ so eagerly, but there are a few good reasons for this.
    #
    # We might be in a situation where the scope never gets torn down
    # properly. In that case we will have an unnecessary strong reference to
    # all objects in the environ (some of which may take a lot of memory) when
    # we're really just interested in a few of them.
    #
    # Keeping the environment around for longer than the request lifecycle is
    # also not necessarily something uWSGI can deal with:
    # https://github.com/unbit/uwsgi/issues/1950

    client_ip = get_client_ip(environ)
    request_url = get_request_url(environ, use_x_forwarded_for)
    query_string = environ.get("QUERY_STRING")
    method = environ.get("REQUEST_METHOD")
    env = dict(_get_environ(environ))
    headers = _filter_headers(dict(_get_headers(environ)))

    def event_processor(event: Event, hint: Dict[str, Any]) -> Event:
        with capture_internal_exceptions():
            # if the code below fails halfway through we at least have some data
            request_info = event.setdefault("request", {})

            if should_send_default_pii():
                user_info = event.setdefault("user", {})
                if client_ip:
                    user_info.setdefault("ip_address", client_ip)

            request_info["url"] = request_url
            request_info["query_string"] = query_string
            request_info["method"] = method
            request_info["env"] = env
            request_info["headers"] = headers

        return event

    return event_processor


def _prepopulate_attributes(
    wsgi_environ: dict[str, str], use_x_forwarded_for: bool = False
) -> dict[str, str]:
    """Extract span attributes from the WSGI environment."""
    attributes = {}

    for property, attr in ENVIRON_TO_ATTRIBUTE.items():
        if wsgi_environ.get(property) is not None:
            attributes[attr] = wsgi_environ[property]

    if wsgi_environ.get("SERVER_PROTOCOL") is not None:
        try:
            proto, version = wsgi_environ["SERVER_PROTOCOL"].split("/")
            attributes["network.protocol.name"] = proto
            attributes["network.protocol.version"] = version
        except Exception:
            attributes["network.protocol.name"] = wsgi_environ["SERVER_PROTOCOL"]

    with capture_internal_exceptions():
        url = get_request_url(wsgi_environ, use_x_forwarded_for)
        query = wsgi_environ.get("QUERY_STRING")
        attributes["url.full"] = f"{url}?{query}"

    attributes.update(
        _request_headers_to_span_attributes(dict(_get_headers(wsgi_environ)))
    )

    return attributes
