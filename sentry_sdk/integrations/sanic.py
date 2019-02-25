import sys
import weakref
from inspect import isawaitable

from sentry_sdk._compat import urlparse, reraise
from sentry_sdk.hub import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations._wsgi_common import RequestExtractor, _filter_headers
from sentry_sdk.integrations.logging import ignore_logger

from sanic import Sanic  # type: ignore
from sanic.exceptions import SanicException  # type: ignore
from sanic.router import Router  # type: ignore
from sanic.handlers import ErrorHandler  # type: ignore

if False:
    from sanic.request import Request  # type: ignore

    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import List
    from typing import Tuple
    from sanic.exceptions import InvalidUsage
    from typing import Optional
    from typing import Union
    from sanic.request import RequestParameters


class SanicIntegration(Integration):
    identifier = "sanic"

    @staticmethod
    def setup_once():
        # type: () -> None
        if sys.version_info < (3, 7):
            # Sanic is async. We better have contextvars or we're going to leak
            # state between requests.
            raise RuntimeError("The sanic integration for Sentry requires Python 3.7+")

        # Sanic 0.8 and older creates a logger named "root" and puts a
        # stringified version of every exception in there (without exc_info),
        # which our error deduplication can't detect.
        #
        # https://github.com/huge-success/sanic/issues/1332
        ignore_logger("root")

        old_handle_request = Sanic.handle_request

        async def sentry_handle_request(self, request, *args, **kwargs):
            # type: (Any, Request, *Any, **Any) -> Any
            hub = Hub.current
            if hub.get_integration(SanicIntegration) is None:
                return old_handle_request(self, request, *args, **kwargs)

            weak_request = weakref.ref(request)

            with Hub(hub) as hub:
                with hub.configure_scope() as scope:
                    scope.add_event_processor(_make_request_processor(weak_request))

                response = old_handle_request(self, request, *args, **kwargs)
                if isawaitable(response):
                    response = await response

                return response

        Sanic.handle_request = sentry_handle_request

        old_router_get = Router.get

        def sentry_router_get(self, request):
            # type: (Any, Request) -> Tuple[Callable, List, Dict[str, str], str]
            rv = old_router_get(self, request)
            hub = Hub.current
            if hub.get_integration(SanicIntegration) is not None:
                with capture_internal_exceptions():
                    with hub.configure_scope() as scope:
                        scope.transaction = rv[0].__name__
            return rv

        Router.get = sentry_router_get

        old_error_handler_lookup = ErrorHandler.lookup

        def sentry_error_handler_lookup(self, exception):
            # type: (Any, Union[ValueError, InvalidUsage]) -> Optional[Callable]
            _capture_exception(exception)
            old_error_handler = old_error_handler_lookup(self, exception)

            if old_error_handler is None:
                return None

            if Hub.current.get_integration(SanicIntegration) is None:
                return old_error_handler

            async def sentry_wrapped_error_handler(request, exception):
                # type: (Request, ValueError) -> Any
                try:
                    response = old_error_handler(request, exception)
                    if isawaitable(response):
                        response = await response
                    return response
                except Exception:
                    exc_info = sys.exc_info()
                    _capture_exception(exc_info)
                    reraise(*exc_info)

            return sentry_wrapped_error_handler

        ErrorHandler.lookup = sentry_error_handler_lookup


def _capture_exception(
    exception  # type: Union[Tuple[type, BaseException, Any], ValueError, InvalidUsage]
):
    # type: (...) -> None
    if isinstance(exception, SanicException):
        return

    hub = Hub.current
    integration = hub.get_integration(SanicIntegration)
    if integration is None:
        return

    with capture_internal_exceptions():
        event, hint = event_from_exception(
            exception,
            client_options=hub.client.options,
            mechanism={"type": "sanic", "handled": False},
        )
        hub.capture_event(event, hint=hint)


def _make_request_processor(weak_request):
    # type: (Callable[[], Request]) -> Callable
    def sanic_processor(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        request = weak_request()
        if request is None:
            return event

        with capture_internal_exceptions():
            extractor = SanicRequestExtractor(request)
            extractor.extract_into_event(event)

            request_info = event["request"]
            urlparts = urlparse.urlsplit(request.url)

            request_info["url"] = "%s://%s%s" % (
                urlparts.scheme,
                urlparts.netloc,
                urlparts.path,
            )

            request_info["query_string"] = urlparts.query
            request_info["method"] = request.method
            request_info["env"] = {"REMOTE_ADDR": request.remote_addr}
            request_info["headers"] = _filter_headers(dict(request.headers))

        return event

    return sanic_processor


class SanicRequestExtractor(RequestExtractor):
    def content_length(self):
        # type: () -> int
        if self.request.body is None:
            return 0
        return len(self.request.body)

    def cookies(self):
        return dict(self.request.cookies)

    def raw_data(self):
        # type: () -> bytes
        return self.request.body

    def form(self):
        # type: () -> RequestParameters
        return self.request.form

    def is_json(self):
        raise NotImplementedError()

    def json(self):
        # type: () -> Optional[Any]
        return self.request.json

    def files(self):
        # type: () -> RequestParameters
        return self.request.files

    def size_of_file(self, file):
        return len(file.body or ())
