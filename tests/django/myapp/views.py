from django.http import HttpResponse
from django.template import engines

import sentry_sdk

sentry_sdk.init()

def self_check(request):
    with sentry_sdk.configure_scope() as scope:
        assert scope._data['transaction'] == self_check
        scope.set_tag('foo', 'bar')

    with sentry_sdk.configure_scope() as scope:
        # ensure scope did not get popped
        assert scope._data['transaction'] == self_check
    return HttpResponse("ok")


def view_exc(request):
    1/0


def get_dsn(request):
    django_engine = engines['django']
    template = django_engine.from_string(
        "{% load sentry %}{% sentry_dsn %}!"
    )
    return HttpResponse(
        template.render({}, request),
        content_type='application/xhtml+xml'
    )
