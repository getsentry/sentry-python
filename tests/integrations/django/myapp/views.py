from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import HttpResponse

import sentry_sdk


def self_check(request):
    with sentry_sdk.configure_scope() as scope:
        assert scope._data["transaction"] == "self_check"
    return HttpResponse("ok")


def view_exc(request):
    1 / 0


def message(request):
    sentry_sdk.capture_message("hi")
    return HttpResponse("ok")


def mylogin(request):
    user = User.objects.create_user('john', 'lennon@thebeatles.com', 'johnpassword')
    user.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, user)
    return HttpResponse("ok")
