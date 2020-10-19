"""
Instrumentation for Django 3.0

Since this file contains `async def` it is conditionally imported in
`sentry_sdk.integrations.django` (depending on the existence of
`django.core.handlers.asgi`.
"""

from sentry_sdk import Hub, _functools
from sentry_sdk._types import MYPY

from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

if MYPY:
    from typing import Any
    from typing import Union

    from django.http.response import HttpResponse


def patch_django_asgi_handler_impl(cls):
    # type: (Any) -> None

    from sentry_sdk.integrations.django import DjangoIntegration

    old_app = cls.__call__

    async def sentry_patched_asgi_handler(self, scope, receive, send):
        # type: (Any, Any, Any, Any) -> Any
        if Hub.current.get_integration(DjangoIntegration) is None:
            return await old_app(self, scope, receive, send)

        middleware = SentryAsgiMiddleware(
            old_app.__get__(self, cls), unsafe_context_data=True
        )._run_asgi3
        return await middleware(scope, receive, send)

    cls.__call__ = sentry_patched_asgi_handler


def patch_get_response_async(cls, _before_get_response):
    # type: (Any, Any) -> None
    old_get_response_async = cls.get_response_async

    async def sentry_patched_get_response_async(self, request):
        # type: (Any, Any) -> Union[HttpResponse, BaseException]
        _before_get_response(request)
        return await old_get_response_async(self, request)

    cls.get_response_async = sentry_patched_get_response_async


def patch_channels_asgi_handler_impl(cls):
    # type: (Any) -> None

    from sentry_sdk.integrations.django import DjangoIntegration

    old_app = cls.__call__

    async def sentry_patched_asgi_handler(self, receive, send):
        # type: (Any, Any, Any) -> Any
        if Hub.current.get_integration(DjangoIntegration) is None:
            return await old_app(self, receive, send)

        middleware = SentryAsgiMiddleware(
            lambda _scope: old_app.__get__(self, cls), unsafe_context_data=True
        )

        return await middleware(self.scope)(receive, send)

    cls.__call__ = sentry_patched_asgi_handler


def wrap_async_view(hub, callback):
    # type: (Hub, Any) -> Any
    @_functools.wraps(callback)
    async def sentry_wrapped_callback(request, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any

        with hub.start_span(
            op="django.view", description=request.resolver_match.view_name
        ):
            return await callback(request, *args, **kwargs)

    return sentry_wrapped_callback
