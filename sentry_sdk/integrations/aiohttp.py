import sys
import weakref

from sentry_sdk._compat import reraise
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception

import asyncio
from aiohttp.web import Application, HTTPError


class AioHttpIntegration(Integration):
    identifier = "aiohttp"

    @staticmethod
    def setup_once():
        if sys.version_info < (3, 7):
            # We better have contextvars or we're going to leak state between
            # requests.
            raise RuntimeError(
                "The aiohttp integration for Sentry requires Python 3.7+"
            )

        ignore_logger("aiohttp.server")

        old_handle = Application._handle

        async def sentry_app_handle(self, request, *args, **kwargs):
            async def inner():
                hub = Hub.current
                if hub.get_integration(AioHttpIntegration) is None:
                    return old_handle(self, request, *args, **kwargs)

                weak_request = weakref.ref(request)

                with Hub(Hub.current) as hub:
                    with hub.configure_scope() as scope:
                        scope.add_event_processor(_make_request_processor(weak_request))

                    try:
                        response = await old_handle(self, request)
                    except HTTPError:
                        raise
                    except Exception:
                        reraise(*_capture_exception(hub))

                    return response

            return await asyncio.create_task(inner())

        Application._handle = sentry_app_handle


def _make_request_processor(weak_request):
    def aiohttp_processor(event, hint):
        request = weak_request()
        if request is None:
            return event

        with capture_internal_exceptions():
            # TODO: Figure out what to do with request body. Methods on request
            # are async, but event processors are not.

            request_info = event.setdefault("request", {})

            if "url" not in request_info:
                request_info["url"] = "%s://%s%s" % (
                    request.scheme,
                    request.host,
                    request.path,
                )

            if "query_string" not in request_info:
                request_info["query_string"] = request.query_string

            if "method" not in request_info:
                request_info["method"] = request.method

            if "env" not in request_info:
                request_info["env"] = {"REMOTE_ADDR": request.remote}

            if "headers" not in request_info:
                request_info["headers"] = _filter_headers(dict(request.headers))

        return event

    return aiohttp_processor


def _capture_exception(hub):
    exc_info = sys.exc_info()
    event, hint = event_from_exception(
        exc_info,
        client_options=hub.client.options,
        mechanism={"type": "aiohttp", "handled": False},
    )
    hub.capture_event(event, hint=hint)
    return exc_info
