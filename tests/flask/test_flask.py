import pytest
from flask import Flask, request

from sentry_sdk import configure_scope
from sentry_sdk.integrations.flask import FlaskSentry

sentry = FlaskSentry()

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config['TESTING'] = True
    sentry.init_app(app)
    return app


def test_has_context(app):
    @app.route('/')
    def index():
        with configure_scope() as scope:
            assert scope._data['transaction'] is 'index'

        return 'ok'

    client = app.test_client()
    response = client.get('/')
    assert response.status_code == 200


@pytest.mark.parametrize('debug', (True, False))
@pytest.mark.parametrize('testing', (True, False))
def test_errors(capture_exceptions, app, debug, testing):
    app.debug = debug
    app.testing = testing
    @app.route('/')
    def index():
        1/0

    client = app.test_client()
    try:
        client.get('/')
    except ZeroDivisionError:
        pass

    exc, = capture_exceptions
    assert isinstance(exc, ZeroDivisionError)
