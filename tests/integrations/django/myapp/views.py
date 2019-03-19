from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import HttpResponse, HttpResponseServerError, HttpResponseNotFound
from django.shortcuts import render
from django.views.generic import ListView

import sentry_sdk


def view_exc(request):
    1 / 0


def message(request):
    sentry_sdk.capture_message("hi")
    return HttpResponse("ok")


def mylogin(request):
    user = User.objects.create_user("john", "lennon@thebeatles.com", "johnpassword")
    user.backend = "django.contrib.auth.backends.ModelBackend"
    login(request, user)
    return HttpResponse("ok")


def handler500(request):
    return HttpResponseServerError("Sentry error: %s" % sentry_sdk.last_event_id())


class ClassBasedView(ListView):
    model = None

    def head(self, *args, **kwargs):
        sentry_sdk.capture_message("hi")
        return HttpResponse("")


def post_echo(request):
    sentry_sdk.capture_message("hi")
    return HttpResponse(request.body)


def handler404(*args, **kwargs):
    sentry_sdk.capture_message("not found", level="error")
    return HttpResponseNotFound("404")


def template_exc(request, *args, **kwargs):
    return render(request, "error.html")
