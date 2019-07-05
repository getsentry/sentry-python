# -*- coding: utf-8 -*-
from __future__ import absolute_import

import sys
import threading
import weakref

from django import VERSION as DJANGO_VERSION  # type: ignore
from django.core import signals  # type: ignore

MYPY = False
if MYPY:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Optional
    from typing import Union

    from django.core.handlers.wsgi import WSGIRequest  # type: ignore
    from django.http.response import HttpResponse  # type: ignore
    from django.http.request import QueryDict  # type: ignore
    from django.utils.datastructures import MultiValueDict  # type: ignore

    from sentry_sdk.integrations.wsgi import _ScopedResponse
    from sentry_sdk.utils import Event, Hint


try:
    from django.urls import resolve  # type: ignore
except ImportError:
    from django.core.urlresolvers import resolve  # type: ignore

from sentry_sdk import Hub
from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.serializer import add_global_repr_processor
from sentry_sdk.tracing import record_sql_queries
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    transaction_from_function,
    walk_exception_chain,
)
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.integrations._wsgi_common import RequestExtractor
from sentry_sdk.integrations._sql_common import format_sql
from sentry_sdk.integrations.django.transactions import LEGACY_RESOLVER
from sentry_sdk.integrations.django.templates import get_template_frame_from_exception


if DJANGO_VERSION < (1, 10):

    def is_authenticated(request_user):
        # type: (Any) -> bool
        return request_user.is_authenticated()


else:

    def is_authenticated(request_user):
        # type: (Any) -> bool
        return request_user.is_authenticated


class DjangoIntegration(Integration):
    identifier = "django"

    transaction_style = None

    def __init__(self, transaction_style="url"):
        # type: (str) -> None
        TRANSACTION_STYLE_VALUES = ("function_name", "url")
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style

    @staticmethod
    def setup_once():
        # type: () -> None
        install_sql_hook()
        # Patch in our custom middleware.

        # logs an error for every 500
        ignore_logger("django.server")
        ignore_logger("django.request")

        from django.core.handlers.wsgi import WSGIHandler

        old_app = WSGIHandler.__call__

        def sentry_patched_wsgi_handler(self, environ, start_response):
            # type: (Any, Dict[str, str], Callable) -> _ScopedResponse
            if Hub.current.get_integration(DjangoIntegration) is None:
                return old_app(self, environ, start_response)

            return SentryWsgiMiddleware(lambda *a, **kw: old_app(self, *a, **kw))(
                environ, start_response
            )

        WSGIHandler.__call__ = sentry_patched_wsgi_handler

        # patch get_response, because at that point we have the Django request
        # object
        from django.core.handlers.base import BaseHandler  # type: ignore

        old_get_response = BaseHandler.get_response

        def sentry_patched_get_response(self, request):
            # type: (Any, WSGIRequest) -> Union[HttpResponse, BaseException]
            hub = Hub.current
            integration = hub.get_integration(DjangoIntegration)
            if integration is not None:
                _patch_drf()

                with hub.configure_scope() as scope:
                    # Rely on WSGI middleware to start a trace
                    try:
                        if integration.transaction_style == "function_name":
                            scope.transaction = transaction_from_function(
                                resolve(request.path).func
                            )
                        elif integration.transaction_style == "url":
                            scope.transaction = LEGACY_RESOLVER.resolve(request.path)
                    except Exception:
                        pass

                    scope.add_event_processor(
                        _make_event_processor(weakref.ref(request), integration)
                    )
            return old_get_response(self, request)

        BaseHandler.get_response = sentry_patched_get_response

        signals.got_request_exception.connect(_got_request_exception)

        @add_global_event_processor
        def process_django_templates(event, hint):
            # type: (Event, Optional[Hint]) -> Optional[Event]
            if hint is None:
                return event

            exc_info = hint.get("exc_info", None)

            if exc_info is None:
                return event

            exception = event.get("exception", None)

            if exception is None:
                return event

            values = exception.get("values", None)

            if values is None:
                return event

            for exception, (_, exc_value, _) in zip(
                reversed(values), walk_exception_chain(exc_info)
            ):
                frame = get_template_frame_from_exception(exc_value)
                if frame is not None:
                    frames = exception.get("stacktrace", {}).get("frames", [])

                    for i in reversed(range(len(frames))):
                        f = frames[i]
                        if (
                            f.get("function") in ("parse", "render")
                            and f.get("module") == "django.template.base"
                        ):
                            i += 1
                            break
                    else:
                        i = len(frames)

                    frames.insert(i, frame)

            return event

        @add_global_repr_processor
        def _django_queryset_repr(value, hint):
            try:
                # Django 1.6 can fail to import `QuerySet` when Django settings
                # have not yet been initialized.
                #
                # If we fail to import, return `NotImplemented`. It's at least
                # unlikely that we have a query set in `value` when importing
                # `QuerySet` fails.
                from django.db.models.query import QuerySet  # type: ignore
            except Exception:
                return NotImplemented

            if not isinstance(value, QuerySet) or value._result_cache:
                return NotImplemented

            # Do not call Hub.get_integration here. It is intentional that
            # running under a new hub does not suddenly start executing
            # querysets. This might be surprising to the user but it's likely
            # less annoying.

            return u"<%s from %s at 0x%x>" % (
                value.__class__.__name__,
                value.__module__,
                id(value),
            )


_DRF_PATCHED = False
_DRF_PATCH_LOCK = threading.Lock()


def _patch_drf():
    """
    Patch Django Rest Framework for more/better request data. DRF's request
    type is a wrapper around Django's request type. The attribute we're
    interested in is `request.data`, which is a cached property containing a
    parsed request body. Reading a request body from that property is more
    reliable than reading from any of Django's own properties, as those don't
    hold payloads in memory and therefore can only be accessed once.

    We patch the Django request object to include a weak backreference to the
    DRF request object, such that we can later use either in
    `DjangoRequestExtractor`.

    This function is not called directly on SDK setup, because importing almost
    any part of Django Rest Framework will try to access Django settings (where
    `sentry_sdk.init()` might be called from in the first place). Instead we
    run this function on every request and do the patching on the first
    request.
    """

    global _DRF_PATCHED

    if _DRF_PATCHED:
        # Double-checked locking
        return

    with _DRF_PATCH_LOCK:
        if _DRF_PATCHED:
            return

        # We set this regardless of whether the code below succeeds or fails.
        # There is no point in trying to patch again on the next request.
        _DRF_PATCHED = True

        with capture_internal_exceptions():
            try:
                from rest_framework.views import APIView  # type: ignore
            except ImportError:
                pass
            else:
                old_drf_initial = APIView.initial

                def sentry_patched_drf_initial(self, request, *args, **kwargs):
                    with capture_internal_exceptions():
                        request._request._sentry_drf_request_backref = weakref.ref(
                            request
                        )
                        pass
                    return old_drf_initial(self, request, *args, **kwargs)

                APIView.initial = sentry_patched_drf_initial


def _make_event_processor(weak_request, integration):
    # type: (Callable[[], WSGIRequest], DjangoIntegration) -> Callable
    def event_processor(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        # if the request is gone we are fine not logging the data from
        # it.  This might happen if the processor is pushed away to
        # another thread.
        request = weak_request()
        if request is None:
            return event

        try:
            drf_request = request._sentry_drf_request_backref()
            if drf_request is not None:
                request = drf_request
        except AttributeError:
            pass

        with capture_internal_exceptions():
            DjangoRequestExtractor(request).extract_into_event(event)

        if _should_send_default_pii():
            with capture_internal_exceptions():
                _set_user_info(request, event)

        return event

    return event_processor


def _got_request_exception(request=None, **kwargs):
    # type: (WSGIRequest, **Any) -> None
    hub = Hub.current
    integration = hub.get_integration(DjangoIntegration)
    if integration is not None:

        # If an integration is there, a client has to be there.
        client = hub.client  # type: Any

        event, hint = event_from_exception(
            sys.exc_info(),
            client_options=client.options,
            mechanism={"type": "django", "handled": False},
        )
        hub.capture_event(event, hint=hint)


class DjangoRequestExtractor(RequestExtractor):
    def env(self):
        # type: () -> Dict[str, str]
        return self.request.META

    def cookies(self):
        # type: () -> Dict[str, str]
        return self.request.COOKIES

    def raw_data(self):
        # type: () -> bytes
        return self.request.body

    def form(self):
        # type: () -> QueryDict
        return self.request.POST

    def files(self):
        # type: () -> MultiValueDict
        return self.request.FILES

    def size_of_file(self, file):
        return file.size

    def parsed_body(self):
        try:
            return self.request.data
        except AttributeError:
            return RequestExtractor.parsed_body(self)


def _set_user_info(request, event):
    # type: (WSGIRequest, Dict[str, Any]) -> None
    user_info = event.setdefault("user", {})

    user = getattr(request, "user", None)

    if user is None or not is_authenticated(user):
        return

    try:
        user_info["id"] = str(user.pk)
    except Exception:
        pass

    try:
        user_info["email"] = user.email
    except Exception:
        pass

    try:
        user_info["username"] = user.get_username()
    except Exception:
        pass


def install_sql_hook():
    # type: () -> None
    """If installed this causes Django's queries to be captured."""
    try:
        from django.db.backends.utils import CursorWrapper  # type: ignore
    except ImportError:
        from django.db.backends.util import CursorWrapper  # type: ignore

    try:
        real_execute = CursorWrapper.execute
        real_executemany = CursorWrapper.executemany
    except AttributeError:
        # This won't work on Django versions < 1.6
        return

    def execute(self, sql, params=None):
        hub = Hub.current
        if hub.get_integration(DjangoIntegration) is None:
            return real_execute(self, sql, params)

        with record_sql_queries(
            hub, [format_sql(sql, params, self.cursor)], label="Django: "
        ):
            return real_execute(self, sql, params)

    def executemany(self, sql, param_list):
        hub = Hub.current
        if hub.get_integration(DjangoIntegration) is None:
            return real_executemany(self, sql, param_list)

        with record_sql_queries(
            hub,
            [format_sql(sql, params, self.cursor) for params in param_list],
            label="Django: ",
        ):
            return real_executemany(self, sql, param_list)

    CursorWrapper.execute = execute
    CursorWrapper.executemany = executemany
    ignore_logger("django.db.backends")
