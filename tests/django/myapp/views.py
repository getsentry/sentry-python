from django.http import HttpResponse

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
