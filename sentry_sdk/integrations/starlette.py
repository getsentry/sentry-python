from __future__ import absolute_import

from sentry_sdk._compat import iteritems
from sentry_sdk._types import MYPY
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations._wsgi_common import (
    _is_json_content_type,
    request_body_within_bounds,
)
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.utils import AnnotatedValue, event_from_exception

if MYPY:
    from typing import Any, Dict


try:
    from starlette.applications import Starlette
    from starlette.datastructures import UploadFile
    from starlette.middleware import Middleware
    from starlette.requests import Request
    from starlette.routing import Match
except ImportError:
    raise DidNotEnable("Starlette is not installed")

TRANSACTION_STYLE_VALUES = ("endpoint", "url")


class StarletteIntegration(Integration):
    identifier = "starlette"

    transaction_style = None

    def __init__(self, transaction_style="endpoint"):
        # type: (str) -> None
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style

    @staticmethod
    def setup_once():
        # type: () -> None

        old_app = Starlette.__call__

        async def sentry_patched_asgi_app(self, scope, receive, send):
            # type: (Any, Any, Any, Any) -> Any
            # TODO(neel): cleanup types
            if Hub.current.get_integration(StarletteIntegration) is None:
                return await old_app(self, scope, receive, send)

            # TODO(anton): make only call to SentryStarletteMiddleware and ditch the _sentry_build_middleware_stack patching.
            middleware = SentryAsgiMiddleware(
                lambda *a, **kw: old_app(self, *a, **kw),
                mechanism_type=StarletteIntegration.identifier,
            )
            middleware.__call__ = middleware._run_asgi3
            return await middleware(scope, receive, send)

        Starlette.__call__ = sentry_patched_asgi_app
        _patch_exception_middleware()

        original_build_middleware_stack = Starlette.build_middleware_stack

        def _sentry_build_middleware_stack(self):
            app = original_build_middleware_stack(self)

            middleware = [
                Middleware(
                    SentryStarletteMiddleware,
                )
            ]
            for cls, options in reversed(middleware):
                app = cls(app=app, **options)

            return app

        Starlette.build_middleware_stack = _sentry_build_middleware_stack


def _capture_exception(exception, handled=False):
    # type: (BaseException, **Any) -> None
    hub = Hub.current
    if hub.get_integration(StarletteIntegration) is None:
        return

    event, hint = event_from_exception(
        exception,
        client_options=hub.client.options,
        mechanism={"type": StarletteIntegration.identifier, "handled": handled},
    )

    hub.capture_event(event, hint=hint)


def _patch_exception_middleware():
    from starlette.exceptions import ExceptionMiddleware

    old_http_exception = ExceptionMiddleware.http_exception

    def sentry_patched_http_exception(self, request, exc):
        _capture_exception(exc, handled=True)
        return old_http_exception(self, request, exc)

    ExceptionMiddleware.http_exception = sentry_patched_http_exception


# TODO: derive from wsgi common request extractor?
class StarletteRequestExtractor:
    def __init__(self, request):
        # type: (Any) -> None
        self.request = request

    async def extract_request_info(self):
        # type: (Dict[str, Any]) -> Any
        client = Hub.current.client
        if client is None:
            return

        data = None

        content_length = await self.content_length()
        request_info = {}

        if _should_send_default_pii():
            request_info["cookies"] = self.cookies()

        if not request_body_within_bounds(client, content_length):
            data = AnnotatedValue(
                "",
                {"rem": [["!config", "x", 0, content_length]], "len": content_length},
            )
        else:
            parsed_body = await self.parsed_body()
            if parsed_body is not None:
                data = parsed_body
            elif await self.raw_data():
                data = AnnotatedValue(
                    "",
                    {"rem": [["!raw", "x", 0, content_length]], "len": content_length},
                )
            else:
                data = None

        if data is not None:
            request_info["data"] = data

        return request_info

    async def content_length(self):
        raw_data = await self.raw_data()
        if raw_data is None:
            return 0
        return len(raw_data)

    def cookies(self):
        return self.request.cookies

    async def raw_data(self):
        return await self.request.body()

    async def form(self):
        """
        curl -X POST http://localhost:8000/upload/somethign -H "Content-Type: application/x-www-form-urlencoded" -d "username=kevin&password=welcome123"
        curl -X POST http://localhost:8000/upload/somethign  -F username=Julian -F password=hello123
        """
        return await self.request.form()

    def is_json(self):
        return _is_json_content_type(self.request.headers.get("content-type"))

    async def json(self):
        """
        curl -X POST localhost:8000/upload/something -H 'Content-Type: application/json' -d '{"login":"my_login","password":"my_password"}'
        """
        if not self.is_json():
            return None

        return await self.request.json()

    async def parsed_body(self):
        """
        curl -X POST http://localhost:8000/upload/somethign  -F username=Julian -F password=hello123 -F photo=@photo.jpg
        """
        form = await self.form()
        if form:
            data = {}
            for key, val in iteritems(form):
                if isinstance(val, UploadFile):
                    size = len(await val.read())
                    data[key] = AnnotatedValue(
                        "", {"len": size, "rem": [["!raw", "x", 0, size]]}
                    )
                else:
                    data[key] = val

            return data

        return await self.json()


class SentryStarletteMiddleware(SentryAsgiMiddleware):
    def __init__(self, app, dispatch=None):
        self.app = app

    async def __call__(self, scope, receive, send):
        print("~~~ running SentryStarletteMiddleware")
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        hub = Hub.current
        integration = hub.get_integration(StarletteIntegration)
        if integration is None:
            return

        with hub.configure_scope() as sentry_scope:
            request = Request(scope, receive=receive, send=send)

            extractor = StarletteRequestExtractor(request)
            info = await extractor.extract_request_info()

            def _make_request_event_processor(req, integration):
                def inner(event, hint):
                    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]

                    # Extract information from request
                    request_info = event.get("request", {})
                    if info:
                        if "cookies" in info:
                            request_info["cookies"] = info["cookies"]
                        if "data" in info:
                            request_info["data"] = info["data"]
                    event["request"] = request_info

                    # Set transaction name
                    router = req.scope["router"]
                    for route in router.routes:
                        match = route.matches(req.scope)
                        if match[0] == Match.FULL:
                            if integration.transaction_style == "endpoint":
                                event["transaction"] = match[1]["endpoint"].__name__
                            elif integration.transaction_style == "url":
                                event["transaction"] = route.path

                    return event

                return inner

            sentry_scope._name = StarletteIntegration.identifier
            sentry_scope.add_event_processor(
                _make_request_event_processor(request, integration)
            )

            await self.app(scope, receive, send)
