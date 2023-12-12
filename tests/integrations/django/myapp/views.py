import json
import threading

from django import VERSION
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseServerError
from django.shortcuts import render
from django.template import Context, Template
from django.template.response import TemplateResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import ListView

try:
    from rest_framework.decorators import api_view
    from rest_framework.response import Response

    @api_view(["POST"])
    def rest_framework_exc(request):
        1 / 0

    @api_view(["POST"])
    def rest_framework_read_body_and_exc(request):
        request.data
        1 / 0

    @api_view(["GET"])
    def rest_hello(request):
        return HttpResponse("ok")

    @api_view(["GET"])
    def rest_permission_denied_exc(request):
        raise PermissionDenied("bye")

    @api_view(["GET"])
    def rest_json_response(request):
        return Response(dict(ok=True))

except ImportError:
    pass


import sentry_sdk
from sentry_sdk import capture_message


@csrf_exempt
def view_exc(request):
    1 / 0


@csrf_exempt
def view_exc_with_msg(request):
    capture_message("oops")
    1 / 0


@cache_page(60)
def cached_view(request):
    return HttpResponse("ok")


def not_cached_view(request):
    return HttpResponse("ok")


def view_with_cached_template_fragment(request):
    template = Template(
        """{% load cache %}
        Not cached content goes here.
        {% cache 500 some_identifier %}
            And here some cached content.
        {% endcache %}
        """
    )
    rendered = template.render(Context({}))
    return HttpResponse(rendered)


# This is a "class based view" as previously found in the sentry codebase. The
# interesting property of this one is that csrf_exempt, as a class attribute,
# is not in __dict__, so regular use of functools.wraps will not forward the
# attribute.
class SentryClassBasedView(object):
    csrf_exempt = True

    def __call__(self, request):
        return HttpResponse("ok")


class SentryClassBasedViewWithCsrf(object):
    def __call__(self, request):
        return HttpResponse("ok")


@csrf_exempt
def read_body_and_view_exc(request):
    request.read()
    1 / 0


@csrf_exempt
def message(request):
    sentry_sdk.capture_message("hi")
    return HttpResponse("ok")


@csrf_exempt
def mylogin(request):
    user = User.objects.create_user("john", "lennon@thebeatles.com", "johnpassword")
    user.backend = "django.contrib.auth.backends.ModelBackend"
    login(request, user)
    return HttpResponse("ok")


@csrf_exempt
def handler500(request):
    return HttpResponseServerError("Sentry error: %s" % sentry_sdk.last_event_id())


class ClassBasedView(ListView):
    model = None

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super(ClassBasedView, self).dispatch(request, *args, **kwargs)

    def head(self, *args, **kwargs):
        sentry_sdk.capture_message("hi")
        return HttpResponse("")

    def post(self, *args, **kwargs):
        return HttpResponse("ok")


@csrf_exempt
def post_echo(request):
    sentry_sdk.capture_message("hi")
    return HttpResponse(request.body)


@csrf_exempt
def handler404(*args, **kwargs):
    sentry_sdk.capture_message("not found", level="error")
    return HttpResponseNotFound("404")


@csrf_exempt
def template_exc(request, *args, **kwargs):
    return render(request, "error.html")


@csrf_exempt
def template_test(request, *args, **kwargs):
    return render(request, "user_name.html", {"user_age": 20})


@csrf_exempt
def custom_ok(request, *args, **kwargs):
    return HttpResponse("custom ok")


@csrf_exempt
def custom_exc(request, *args, **kwargs):
    1 / 0


@csrf_exempt
def template_test2(request, *args, **kwargs):
    return TemplateResponse(
        request, ("user_name.html", "another_template.html"), {"user_age": 25}
    )


@csrf_exempt
def template_test3(request, *args, **kwargs):
    from sentry_sdk import Hub

    hub = Hub.current
    capture_message(hub.get_traceparent() + "\n" + hub.get_baggage())
    return render(request, "trace_meta.html", {})


@csrf_exempt
def postgres_select(request, *args, **kwargs):
    from django.db import connections

    cursor = connections["postgres"].cursor()
    cursor.execute("SELECT 1;")
    return HttpResponse("ok")


@csrf_exempt
def postgres_select_orm(request, *args, **kwargs):
    user = User.objects.using("postgres").all().first()
    return HttpResponse("ok {}".format(user))


@csrf_exempt
def permission_denied_exc(*args, **kwargs):
    raise PermissionDenied("bye")


def csrf_hello_not_exempt(*args, **kwargs):
    return HttpResponse("ok")


def thread_ids_sync(*args, **kwargs):
    response = json.dumps(
        {
            "main": threading.main_thread().ident,
            "active": threading.current_thread().ident,
        }
    )
    return HttpResponse(response)


if VERSION >= (3, 1):
    # Use exec to produce valid Python 2
    exec(
        """async def async_message(request):
    sentry_sdk.capture_message("hi")
    return HttpResponse("ok")"""
    )

    exec(
        """async def my_async_view(request):
    import asyncio
    await asyncio.sleep(1)
    return HttpResponse('Hello World')"""
    )

    exec(
        """async def thread_ids_async(request):
    response = json.dumps({
        "main": threading.main_thread().ident,
        "active": threading.current_thread().ident,
    })
    return HttpResponse(response)"""
    )

    exec(
        """async def post_echo_async(request):
    sentry_sdk.capture_message("hi")
    return HttpResponse(request.body)
post_echo_async.csrf_exempt = True"""
    )
else:
    async_message = None
    my_async_view = None
    thread_ids_async = None
    post_echo_async = None
