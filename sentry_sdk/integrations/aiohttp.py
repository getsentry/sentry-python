import sys
import weakref

from sentry_sdk._compat import reraise
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    HAS_REAL_CONTEXTVARS,
)

import asyncio
from aiohttp.web import Application, HTTPException  # type: ignore

MYPY = False
if MYPY:
    from aiohttp.web_request import Request  # type: ignore
    from typing import Any
    from typing import Dict
    from typing import Tuple
    from typing import Callable

    from sentry_sdk.utils import ExcInfo


class AioHttpIntegration(Integration):
    identifier = "aiohttp"

    @staticmethod
    def setup_once():
        # type: () -> None
        if not HAS_REAL_CONTEXTVARS:
            # We better have contextvars or we're going to leak state between
            # requests.
            raise RuntimeError(
                "The aiohttp integration for Sentry requires Python 3.7+ "
                " or aiocontextvars package"
            )

        ignore_logger("aiohttp.server")

        old_handle = Application._handle

        async def sentry_app_handle(self, request, *args, **kwargs):
            # type: (Any, Request, *Any, **Any) -> Any
            async def inner():
                # type: () -> Any
                hub = Hub.current
                if hub.get_integration(AioHttpIntegration) is None:
                    return await old_handle(self, request, *args, **kwargs)

                weak_request = weakref.ref(request)

                with Hub(Hub.current) as hub:
                    with hub.configure_scope() as scope:
                        scope.clear_breadcrumbs()
                        scope.add_event_processor(_make_request_processor(weak_request))

                    try:
                        response = await old_handle(self, request)
                    except HTTPException:
                        raise
                    except Exception:
                        reraise(*_capture_exception(hub))

                    return response

            # Explicitly wrap in task such that current contextvar context is
            # copied. Just doing `return await inner()` will leak scope data
            # between requests.
            return await asyncio.get_event_loop().create_task(inner())

        Application._handle = sentry_app_handle


def _make_request_processor(weak_request):
    # type: (Callable[[], Request]) -> Callable
    def aiohttp_processor(
        event,  # type: Dict[str, Any]
        hint,  # type: Dict[str, Tuple[type, BaseException, Any]]
    ):
        # type: (...) -> Dict[str, Any]
        request = weak_request()
        if request is None:
            return event

        with capture_internal_exceptions():
            # TODO: Figure out what to do with request body. Methods on request
            # are async, but event processors are not.

            request_info = event.setdefault("request", {})

            request_info["url"] = "%s://%s%s" % (
                request.scheme,
                request.host,
                request.path,
            )

            request_info["query_string"] = request.query_string
            request_info["method"] = request.method
            request_info["env"] = {"REMOTE_ADDR": request.remote}
            request_info["headers"] = _filter_headers(dict(request.headers))

        return event

    return aiohttp_processor


def _capture_exception(hub):
    # type: (Hub) -> ExcInfo
    exc_info = sys.exc_info()
    event, hint = event_from_exception(
        exc_info,
        client_options=hub.client.options,  # type: ignore
        mechanism={"type": "aiohttp", "handled": False},
    )
    hub.capture_event(event, hint=hint)
    return exc_info
