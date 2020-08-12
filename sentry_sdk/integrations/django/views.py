import functools

from sentry_sdk.hub import Hub
from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any

    from django.core.handlers.wsgi import WSGIRequest
    from django.urls.resolvers import ResolverMatch


def patch_resolve_request():
    # type: () -> None
    from django.core.handlers.base import BaseHandler
    from sentry_sdk.integrations.django import DjangoIntegration

    try:
        old_resolve_request = BaseHandler.resolve_request
    except AttributeError:
        # Django 3+ only
        return

    def resolve_request(self, request):
        # type: (BaseHandler, WSGIRequest) -> ResolverMatch
        hub = Hub.current
        integration = hub.get_integration(DjangoIntegration)

        if integration is None or not integration.middleware_spans:
            return old_resolve_request(self, request)

        with hub.start_span(op="django.view.resolve"):
            return _wrap_resolver_match(hub, old_resolve_request(self, request))

    BaseHandler.resolve_request = resolve_request


def _wrap_resolver_match(hub, resolver_match):
    # type: (Hub, ResolverMatch) -> ResolverMatch

    # XXX: The wrapper function is created for every request. Find more
    # efficient way to wrap views (or build a cache?)

    old_callback = resolver_match.func

    @functools.wraps(old_callback)
    def callback(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        with hub.start_span(op="django.view", description=resolver_match.view_name):
            return old_callback(*args, **kwargs)

    resolver_match.func = callback

    return resolver_match
