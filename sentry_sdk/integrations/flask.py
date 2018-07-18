from __future__ import absolute_import

from sentry_sdk import capture_exception, configure_scope, get_current_hub

from flask import request, _app_ctx_stack
from flask.signals import appcontext_pushed, appcontext_popped, got_request_exception

class FlaskSentry(object):
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        if not hasattr(app, 'extensions'):
            app.extensions = {}
        elif app.extensions.get(__name__, None):
            raise RuntimeError('Sentry registration is already registered')
        app.extensions[__name__] = True

        appcontext_pushed.connect(self.push_appctx, sender=app)
        app.teardown_appcontext(self.pop_appctx)
        got_request_exception.connect(self.capture_exception, sender=app)
        app.before_request(self.before_request)

    def push_appctx(self, *args, **kwargs):
        assert getattr(
            _app_ctx_stack.top,
            '_sentry_app_scope_manager',
            None
        ) is None, 'race condition'
        _app_ctx_stack.top._sentry_app_scope_manager = \
            get_current_hub().push_scope().__enter__()

    def pop_appctx(self, exception):
        assert getattr(
            _app_ctx_stack.top,
            '_sentry_app_scope_manager',
            None
        ) is not None, 'race condition'
        _app_ctx_stack.top._sentry_app_scope_manager.__exit__(None, None, None)
        _app_ctx_stack.top._sentry_app_scope_manager = None

    def capture_exception(self, sender, exception, **kwargs):
        capture_exception(exception)

    def before_request(self, *args, **kwargs):
        if request.url_rule:
            with configure_scope() as scope:
                scope.transaction = request.url_rule.endpoint
