from django import VERSION
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseServerError
from django.shortcuts import render
from django.template.response import TemplateResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import ListView

try:
    from rest_framework.decorators import api_view

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

except ImportError:
    pass


import sentry_sdk


@csrf_exempt
def view_exc(request):
    1 / 0


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
def postgres_select(request, *args, **kwargs):
    from django.db import connections

    cursor = connections["postgres"].cursor()
    cursor.execute("SELECT 1;")
    return HttpResponse("ok")


@csrf_exempt
def permission_denied_exc(*args, **kwargs):
    raise PermissionDenied("bye")


def csrf_hello_not_exempt(*args, **kwargs):
    return HttpResponse("ok")


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
else:
    async_message = None
    my_async_view = None
