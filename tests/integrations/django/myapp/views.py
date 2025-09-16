import asyncio
import json
import threading
import time

from django.contrib.auth import login
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.dispatch import Signal
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseServerError
from django.shortcuts import render
from django.template import Context, Template
from django.template.response import TemplateResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import ListView


from tests.integrations.django.myapp.signals import (
    myapp_custom_signal,
    myapp_custom_signal_silenced,
)

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
class SentryClassBasedView:
    csrf_exempt = True

    def __call__(self, request):
        return HttpResponse("ok")


class SentryClassBasedViewWithCsrf:
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
def nomessage(request):
    return HttpResponse("ok")


@csrf_exempt
def view_with_signal(request):
    custom_signal = Signal()
    custom_signal.send(sender="hello")
    return HttpResponse("ok")


@csrf_exempt
def mylogin(request):
    user = User.objects.create_user("john", "lennon@thebeatles.com", "johnpassword")
    user.backend = "django.contrib.auth.backends.ModelBackend"
    login(request, user)
    return HttpResponse("ok")


@csrf_exempt
def handler500(request):
    return HttpResponseServerError("Sentry error.")


class ClassBasedView(ListView):
    model = None

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

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
    traceparent = sentry_sdk.get_current_scope().get_traceparent()
    if traceparent is None:
        traceparent = sentry_sdk.get_isolation_scope().get_traceparent()

    baggage = sentry_sdk.get_current_scope().get_baggage()
    if baggage is None:
        baggage = sentry_sdk.get_isolation_scope().get_baggage()

    capture_message(traceparent + "\n" + baggage.serialize())
    return render(request, "trace_meta.html", {})


@csrf_exempt
def template_test4(request, *args, **kwargs):
    return TemplateResponse(
        request,
        "user_name.html",
        {
            "user_age": 25,
            "complex_context": lambda x: time.sleep(10),
            "complex_list": [1, 2, 3, lambda x: time.sleep(10)],
            "complex_dict": {
                "a": 1,
                "d": lambda x: time.sleep(10),
            },
            "none_context": None,
        },
    )


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


async def async_message(request):
    sentry_sdk.capture_message("hi")
    return HttpResponse("ok")


async def my_async_view(request):
    await asyncio.sleep(1)
    return HttpResponse("Hello World")


async def simple_async_view(request):
    return HttpResponse("Simple Hello World")


async def thread_ids_async(request):
    response = json.dumps(
        {
            "main": threading.main_thread().ident,
            "active": threading.current_thread().ident,
        }
    )
    return HttpResponse(response)


async def post_echo_async(request):
    sentry_sdk.capture_message("hi")
    return HttpResponse(request.body)


post_echo_async.csrf_exempt = True


@csrf_exempt
def send_myapp_custom_signal(request):
    myapp_custom_signal.send(sender="hello")
    myapp_custom_signal_silenced.send(sender="hello")
    return HttpResponse("ok")
