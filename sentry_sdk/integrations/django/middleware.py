"""
Create spans from Django middleware invocations
"""

from django import VERSION as DJANGO_VERSION

from sentry_sdk import Hub
from sentry_sdk._functools import wraps
from sentry_sdk._types import MYPY
from sentry_sdk.utils import (
    ContextVar,
    transaction_from_function,
    capture_internal_exceptions,
)

if MYPY:
    from typing import Any
    from typing import Callable
    from typing import TypeVar

    F = TypeVar("F", bound=Callable[..., Any])

_import_string_should_wrap_middleware = ContextVar(
    "import_string_should_wrap_middleware"
)

if DJANGO_VERSION < (1, 7):
    import_string_name = "import_by_path"
else:
    import_string_name = "import_string"


def patch_django_middlewares():
    # type: () -> None
    from django.core.handlers import base

    old_import_string = getattr(base, import_string_name)

    def sentry_patched_import_string(dotted_path):
        # type: (str) -> Any
        rv = old_import_string(dotted_path)

        if _import_string_should_wrap_middleware.get(None):
            rv = _wrap_middleware(rv, dotted_path)

        return rv

    setattr(base, import_string_name, sentry_patched_import_string)

    old_load_middleware = base.BaseHandler.load_middleware

    def sentry_patched_load_middleware(*args, **kwargs):
        # type: (Any, Any) -> Any
        _import_string_should_wrap_middleware.set(True)
        try:
            return old_load_middleware(*args, **kwargs)
        finally:
            _import_string_should_wrap_middleware.set(False)

    base.BaseHandler.load_middleware = sentry_patched_load_middleware


def _wrap_middleware(middleware, middleware_name):
    # type: (Any, str) -> Any
    from sentry_sdk.integrations.django import DjangoIntegration

    def _check_middleware_span(old_method):
        hub = Hub.current
        integration = hub.get_integration(DjangoIntegration)
        if integration is None or not integration.middleware_spans:
            return None

        function_name = transaction_from_function(old_method)

        description = middleware_name
        function_basename = getattr(old_method, "__name__", None)
        if function_basename:
            description = "{}.{}".format(description, function_basename)

        middleware_span = hub.start_span(op="django.middleware", description=description)
        middleware_span.set_tag("django.function_name", function_name)
        middleware_span.set_tag("django.middleware_name", middleware_name)

        return middleware_span

    def _get_wrapped_method(old_method):
        # type: (F) -> F
        with capture_internal_exceptions():

            def sentry_wrapped_method(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                middleware_span = _check_middleware_span(old_method)

                if middleware_span is None:
                    return old_method(*args, **kwargs)

                with middleware_span:
                    return old_method(*args, **kwargs)

            try:
                # fails for __call__ of function on Python 2 (see py2.7-django-1.11)
                sentry_wrapped_method = wraps(old_method)(sentry_wrapped_method)

                # Necessary for Django 3.1
                sentry_wrapped_method.__self__ = old_method.__self__  # type: ignore
            except Exception:
                pass

            return sentry_wrapped_method  # type: ignore

        return old_method

    import asyncio
    from django.utils.deprecation import MiddlewareMixin

    class SentryWrappingMiddleware(MiddlewareMixin):

        def __init__(self, *args, **kwargs):
            # type: (*Any, **Any) -> None
            self._inner = middleware(*args, **kwargs)
            self._call_method = None
            self._acall_method = None

        # We need correct behavior for `hasattr()`, which we can only determine
        # when we have an instance of the middleware we're wrapping.
        def __getattr__(self, method_name):
            # type: (str) -> Any
            if method_name not in (
                    "process_request",
                    "process_view",
                    "process_template_response",
                    "process_response",
                    "process_exception",
            ):
                raise AttributeError()

            old_method = getattr(self._inner, method_name)
            rv = _get_wrapped_method(old_method)
            self.__dict__[method_name] = rv
            return rv

        def __call__(self, *args, **kwargs):
            # type: (*Any, **Any) -> Any
            # Readability refactor
            if asyncio.iscoroutinefunction(self.get_response):
                return self.__acall__(*args, **kwargs)

            f = self._call_method
            if f is None:
                self._call_method = f = _get_wrapped_method(self._inner.__call__)
            return f(*args, **kwargs)

        async def __acall__(self, *args, **kwargs):
            f = self._acall_method
            if f is None:
                self._acall_method = f = self._inner.__acall__

            middleware_span = _check_middleware_span(old_method=f)

            if middleware_span is None:
                return await f(*args, **kwargs)

            with middleware_span:
                return await f(*args, **kwargs)

    if hasattr(middleware, "__name__"):
        SentryWrappingMiddleware.__name__ = middleware.__name__

    return SentryWrappingMiddleware
