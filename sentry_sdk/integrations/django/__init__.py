# -*- coding: utf-8 -*-
from __future__ import absolute_import

import sys
import weakref

from django import VERSION as DJANGO_VERSION  # type: ignore
from django.db.models.query import QuerySet  # type: ignore
from django.core import signals  # type: ignore

if False:
    from typing import Any
    from typing import Dict
    from typing import Tuple
    from typing import Union
    from sentry_sdk.integrations.wsgi import _ScopedResponse
    from typing import Callable
    from django.core.handlers.wsgi import WSGIRequest  # type: ignore
    from django.http.response import HttpResponse  # type: ignore
    from django.http.request import QueryDict  # type: ignore
    from django.utils.datastructures import MultiValueDict  # type: ignore
    from typing import List


try:
    from django.urls import resolve  # type: ignore
except ImportError:
    from django.core.urlresolvers import resolve  # type: ignore

from sentry_sdk import Hub
from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.utils import (
    add_global_repr_processor,
    capture_internal_exceptions,
    event_from_exception,
    safe_repr,
    format_and_strip,
    transaction_from_function,
    walk_exception_chain,
)
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.integrations._wsgi_common import RequestExtractor
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
                with hub.configure_scope() as scope:
                    scope.add_event_processor(
                        _make_event_processor(weakref.ref(request), integration)
                    )
            return old_get_response(self, request)

        BaseHandler.get_response = sentry_patched_get_response

        signals.got_request_exception.connect(_got_request_exception)

        @add_global_event_processor
        def process_django_templates(event, hint):
            # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
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
            if integration.transaction_style == "function_name":
                event["transaction"] = transaction_from_function(
                    resolve(request.path).func
                )
            elif integration.transaction_style == "url":
                event["transaction"] = LEGACY_RESOLVER.resolve(request.path)
        except Exception:
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
        event, hint = event_from_exception(
            sys.exc_info(),
            client_options=hub.client.options,
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


class _FormatConverter(object):
    def __init__(self, param_mapping):
        # type: (Dict[str, int]) -> None

        self.param_mapping = param_mapping
        self.params = []  # type: List[Any]

    def __getitem__(self, val):
        # type: (str) -> str
        self.params.append(self.param_mapping.get(val))
        return "%s"


def format_sql(sql, params):
    # type: (Any, Any) -> Tuple[str, List[str]]
    rv = []

    if isinstance(params, dict):
        # convert sql with named parameters to sql with unnamed parameters
        conv = _FormatConverter(params)
        if params:
            sql = sql % conv
            params = conv.params
        else:
            params = ()

    for param in params or ():
        if param is None:
            rv.append("NULL")
        param = safe_repr(param)
        rv.append(param)

    return sql, rv


def record_sql(sql, params, cursor=None):
    # type: (Any, Any, Any) -> None
    hub = Hub.current
    if hub.get_integration(DjangoIntegration) is None:
        return

    real_sql = None
    real_params = None

    try:
        # Prefer our own SQL formatting logic because it's the only one that
        # has proper value trimming.
        real_sql, real_params = format_sql(sql, params)
        if real_sql:
            real_sql = format_and_strip(real_sql, real_params)
    except Exception:
        pass

    if not real_sql and cursor and hasattr(cursor, "mogrify"):
        # If formatting failed and we're using psycopg2, it could be that we're
        # looking at a query that uses Composed objects. Use psycopg2's mogrify
        # function to format the query. We lose per-parameter trimming but gain
        # accuracy in formatting.
        #
        # This is intentionally the second choice because we assume Composed
        # queries are not widely used, while per-parameter trimming is
        # generally highly desirable.
        try:
            if cursor and hasattr(cursor, "mogrify"):
                real_sql = cursor.mogrify(sql, params)
                if isinstance(real_sql, bytes):
                    real_sql = real_sql.decode(cursor.connection.encoding)
        except Exception:
            pass

    if real_sql:
        with capture_internal_exceptions():
            hub.add_breadcrumb(message=real_sql, category="query")


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

    def record_many_sql(sql, param_list, cursor):
        for params in param_list:
            record_sql(sql, params, cursor)

    def execute(self, sql, params=None):
        try:
            return real_execute(self, sql, params)
        finally:
            record_sql(sql, params, self.cursor)

    def executemany(self, sql, param_list):
        try:
            return real_executemany(self, sql, param_list)
        finally:
            record_many_sql(sql, param_list, self.cursor)

    CursorWrapper.execute = execute
    CursorWrapper.executemany = executemany
    ignore_logger("django.db.backends")
