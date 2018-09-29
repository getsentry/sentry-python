# -*- coding: utf-8 -*-
from __future__ import absolute_import

import sys
import weakref

from django import VERSION as DJANGO_VERSION
from django.core import signals

try:
    from django.urls import resolve
except ImportError:
    from django.core.urlresolvers import resolve

from sentry_sdk import Hub, configure_scope, add_breadcrumb
from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    safe_repr,
    format_and_strip,
)
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations._wsgi import RequestExtractor, run_wsgi_app


if DJANGO_VERSION < (1, 10):

    def is_authenticated(request_user):
        return request_user.is_authenticated()


else:

    def is_authenticated(request_user):
        return request_user.is_authenticated


class DjangoIntegration(Integration):
    identifier = "django"

    def install(self):
        install_sql_hook()
        # Patch in our custom middleware.

        from django.core.handlers.wsgi import WSGIHandler

        old_app = WSGIHandler.__call__

        def sentry_patched_wsgi_handler(self, environ, start_response):
            return run_wsgi_app(
                lambda *a, **kw: old_app(self, *a, **kw), environ, start_response
            )

        WSGIHandler.__call__ = sentry_patched_wsgi_handler

        # patch get_response, because at that point we have the Django request
        # object
        from django.core.handlers.base import BaseHandler

        old_get_response = BaseHandler.get_response

        def sentry_patched_get_response(self, request):
            with configure_scope() as scope:
                scope.add_event_processor(_make_event_processor(weakref.ref(request)))
            return old_get_response(self, request)

        BaseHandler.get_response = sentry_patched_get_response

        signals.got_request_exception.connect(_got_request_exception)


def _make_event_processor(weak_request):
    def event_processor(event, hint):
        # if the request is gone we are fine not logging the data from
        # it.  This might happen if the processor is pushed away to
        # another thread.
        request = weak_request()
        if request is None:
            return event

        if "transaction" not in event:
            try:
                event["transaction"] = resolve(request.path).func.__name__
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
    hub = Hub.current
    event, hint = event_from_exception(
        sys.exc_info(),
        with_locals=hub.client.options["with_locals"],
        mechanism={"type": "django", "handled": False},
    )

    hub.capture_event(event, hint=hint)


class DjangoRequestExtractor(RequestExtractor):
    def url(self):
        return self.request.build_absolute_uri(self.request.path)

    def env(self):
        return self.request.META

    def cookies(self):
        return self.request.COOKIES

    def raw_data(self):
        return self.request.body

    def form(self):
        return self.request.POST

    def files(self):
        return self.request.FILES

    def size_of_file(self, file):
        return file.size


def _set_user_info(request, event):
    user_info = event.setdefault("user", {})

    user = getattr(request, "user", None)

    if user is None or not is_authenticated(user):
        return

    if "email" not in user_info:
        try:
            user_info["email"] = user.email
        except Exception:
            pass

    if "username" not in user_info:
        try:
            user_info["username"] = user.get_username()
        except Exception:
            pass


class _FormatConverter(object):
    def __init__(self, param_mapping):
        self.param_mapping = param_mapping
        self.params = []

    def __getitem__(self, val):
        self.params.append(self.param_mapping.get(val))
        return "%s"


def format_sql(sql, params):
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


def record_sql(sql, params):
    real_sql, real_params = format_sql(sql, params)

    if real_params:
        try:
            real_sql = format_and_strip(real_sql, real_params)
        except Exception:
            pass

    # maybe category to 'django.%s.%s' % (vendor, alias or
    #   'default') ?

    add_breadcrumb(message=real_sql, category="query")


def install_sql_hook():
    """If installed this causes Django's queries to be captured."""
    try:
        from django.db.backends.utils import CursorWrapper
    except ImportError:
        from django.db.backends.util import CursorWrapper

    try:
        real_execute = CursorWrapper.execute
        real_executemany = CursorWrapper.executemany
    except AttributeError:
        # This won't work on Django versions < 1.6
        return

    def record_many_sql(sql, param_list):
        for params in param_list:
            record_sql(sql, params)

    def execute(self, sql, params=None):
        try:
            return real_execute(self, sql, params)
        finally:
            record_sql(sql, params)

    def executemany(self, sql, param_list):
        try:
            return real_executemany(self, sql, param_list)
        finally:
            record_many_sql(sql, param_list)

    CursorWrapper.execute = execute
    CursorWrapper.executemany = executemany
    ignore_logger("django.db.backends")
