from threading import Lock, local

from django.apps import AppConfig
from django.core import signals
from django.conf import settings
from django.urls import resolve

from .. import get_current_hub, configure_scope, capture_exception

try:
    # Django >= 1.10
    from django.utils.deprecation import MiddlewareMixin
except ImportError:
    # Not required for Django <= 1.9, see:
    # https://docs.djangoproject.com/en/1.10/topics/http/middleware/#upgrading-pre-django-1-10-style-middleware
    MiddlewareMixin = object


def _get_transaction_from_request(request):
    return resolve(request.path).func

_request_scope = local()


class SentryMiddleware(MiddlewareMixin):
    def process_view(self, request, func, args, kwargs):
        try:
            with configure_scope() as scope:
                scope.transaction = _get_transaction_from_request(request)
        except Exception:
            capture_exception()


def _request_started(*args, **kwargs):
    assert getattr(_request_scope, 'manager', None) is None, 'race condition'
    _request_scope.manager = get_current_hub().push_scope().__enter__()


def _request_finished(*args, **kwargs):
    assert getattr(_request_scope, 'manager', None) is not None, 'race condition'
    _request_scope.manager.__exit__(None, None, None)
    _request_scope.manager = None


def _got_request_exception(request=None, **kwargs):
    capture_exception()


MIDDLEWARE_NAME = 'sentry_sdk.integrations.django.SentryMiddleware'

CONFLICTING_MIDDLEWARE = (
    'raven.contrib.django.middleware.SentryMiddleware',
    'raven.contrib.django.middleware.SentryLogMiddleware'
) + (MIDDLEWARE_NAME,)

_installer_lock = Lock()
_installed = False


def initialize():
    global _installed
    with _installer_lock:
        if _installed:
            return
        _initialize_impl()
        _installed = True


def _initialize_impl():
    # default settings.MIDDLEWARE is None
    if getattr(settings, 'MIDDLEWARE', None):
        middleware_attr = 'MIDDLEWARE'
    else:
        middleware_attr = 'MIDDLEWARE_CLASSES'

    # make sure to get an empty tuple when attr is None
    middleware = getattr(settings, middleware_attr, ()) or ()
    conflicts = set(CONFLICTING_MIDDLEWARE).intersection(set(middleware))
    if conflicts:
        raise RuntimeError('Other sentry-middleware already registered: %s' %
                           conflicts)

    setattr(settings,
            middleware_attr,
            [MIDDLEWARE_NAME] + list(middleware))

    signals.request_started.connect(_request_started)
    signals.request_finished.connect(_request_finished)
    signals.got_request_exception.connect(_got_request_exception)


default_app_config = 'sentry_sdk.integrations.django.SentryConfig'


class SentryConfig(AppConfig):
    name = 'sentry_sdk.integrations.django'
    label = 'sentry_sdk_integrations_django'
    verbose_name = 'Sentry'

    def ready(self):
        initialize()
