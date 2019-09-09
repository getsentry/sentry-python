from functools import wraps

from sentry_sdk import Hub
from sentry_sdk.utils import ContextVar, transaction_from_function

_import_string_should_wrap_middleware = ContextVar(
    "import_string_should_wrap_middleware"
)


def patch_django_middlewares():
    from django.core.handlers import base

    old_import_string = base.import_string

    def sentry_patched_import_string(dotted_path):
        rv = old_import_string(dotted_path)

        if _import_string_should_wrap_middleware.get(None):
            rv = _wrap_middleware(rv, dotted_path)

        return rv

    base.import_string = sentry_patched_import_string

    old_load_middleware = base.BaseHandler.load_middleware

    def sentry_patched_load_middleware(self):
        _import_string_should_wrap_middleware.set(True)
        try:
            return old_load_middleware(self)
        finally:
            _import_string_should_wrap_middleware.set(False)

    base.BaseHandler.load_middleware = sentry_patched_load_middleware


def _wrap_middleware(middleware, middleware_name):
    from sentry_sdk.integrations.django import DjangoIntegration

    class SentryWrappingMiddleware(object):
        def __init__(self, *args, **kwargs):
            self._inner = middleware(*args, **kwargs)

        def __getattr__(self, method_name):
            if method_name not in (
                "process_request",
                "process_view",
                "process_template_response",
                "process_response",
                "process_exception",
            ):
                raise AttributeError()

            old_method = getattr(self._inner, method_name)

            @wraps(old_method)
            def sentry_wrapped_method(*args, **kwargs):
                hub = Hub.current
                integration = hub.get_integration(DjangoIntegration)
                if integration is None or not integration.middleware_spans:
                    return old_method(*args, **kwargs)

                function_name = transaction_from_function(old_method)

                with hub.start_span(
                    op="django.middleware", description=function_name
                ) as span:
                    span.set_tag("django.function_name", function_name)
                    span.set_tag("django.middleware_name", middleware_name)
                    return old_method(*args, **kwargs)

            self.__dict__[method_name] = sentry_wrapped_method
            return sentry_wrapped_method

        def __call__(self, *args, **kwargs):
            return self._inner(*args, **kwargs)

    if hasattr(middleware, "__name__"):
        SentryWrappingMiddleware.__name__ = middleware.__name__

    return SentryWrappingMiddleware
