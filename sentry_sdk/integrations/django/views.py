from sentry_sdk.hub import Hub
from sentry_sdk._types import MYPY
from sentry_sdk import _functools

if MYPY:
    from typing import Any

    from django.urls.resolvers import ResolverMatch


def patch_resolver():
    # type: () -> None
    try:
        from django.urls.resolvers import URLResolver
    except ImportError:
        try:
            from django.urls.resolvers import RegexURLResolver as URLResolver
        except ImportError:
            from django.core.urlresolvers import RegexURLResolver as URLResolver

    from sentry_sdk.integrations.django import DjangoIntegration

    old_resolve = URLResolver.resolve

    def resolve(self, path):
        # type: (URLResolver, Any) -> ResolverMatch
        hub = Hub.current
        integration = hub.get_integration(DjangoIntegration)

        if integration is None or not integration.middleware_spans:
            return old_resolve(self, path)

        return _wrap_resolver_match(hub, old_resolve(self, path))

    URLResolver.resolve = resolve


def _wrap_resolver_match(hub, resolver_match):
    # type: (Hub, ResolverMatch) -> ResolverMatch

    # XXX: The wrapper function is created for every request. Find more
    # efficient way to wrap views (or build a cache?)

    old_callback = resolver_match.func

    # Explicitly forward `csrf_exempt` in case it is not an attribute in
    # callback.__dict__, but rather a class attribute (on a class
    # implementing __call__) such as this:
    #
    #     class Foo(object):
    #         csrf_exempt = True
    #
    #         def __call__(self, request): ...
    #
    # We have had this in the Sentry codebase (for no good reason, but
    # nevertheless we broke user code)
    assigned = _functools.WRAPPER_ASSIGNMENTS + ("csrf_exempt",)

    @_functools.wraps(old_callback, assigned=assigned)
    def callback(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        with hub.start_span(op="django.view", description=resolver_match.view_name):
            return old_callback(*args, **kwargs)

    resolver_match.func = callback

    return resolver_match
