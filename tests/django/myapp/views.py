from django.http import HttpResponse
from django.template import Template, Context

import sentry_sdk

sentry_sdk.init()

def self_check(request):
    with sentry_sdk.configure_scope() as scope:
        assert scope._data['transaction'] == self_check
    return HttpResponse("ok")


def view_exc(request):
    1/0


def get_dsn(request):
    template = Template(
        "{% load sentry %}{% sentry_dsn %}!"
    )
    return HttpResponse(
        template.render(Context()),
        content_type='application/xhtml+xml'
    )
