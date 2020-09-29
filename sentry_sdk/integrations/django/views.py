from sentry_sdk.hub import Hub
from sentry_sdk._types import MYPY
from sentry_sdk import _functools

if MYPY:
    from typing import Any


def patch_views():
    # type: () -> None

    from django.core.handlers.base import BaseHandler
    from sentry_sdk.integrations.django import DjangoIntegration

    old_make_view_atomic = BaseHandler.make_view_atomic

    @_functools.wraps(old_make_view_atomic)
    def sentry_patched_make_view_atomic(self, *args, **kwargs):
        # type: (Any, *Any, **Any) -> Any
        callback = old_make_view_atomic(self, *args, **kwargs)

        # XXX: The wrapper function is created for every request. Find more
        # efficient way to wrap views (or build a cache?)

        hub = Hub.current
        integration = hub.get_integration(DjangoIntegration)

        if integration is not None and integration.middleware_spans:

            @_functools.wraps(callback)
            def sentry_wrapped_callback(request, *args, **kwargs):
                # type: (Any, *Any, **Any) -> Any
                with hub.start_span(
                    op="django.view", description=request.resolver_match.view_name
                ):
                    return callback(request, *args, **kwargs)

        else:
            sentry_wrapped_callback = callback

        return sentry_wrapped_callback

    BaseHandler.make_view_atomic = sentry_patched_make_view_atomic
