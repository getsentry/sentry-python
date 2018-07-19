import pytest
from flask import Flask, request

from flask_login import LoginManager, login_user

from sentry_sdk import configure_scope, capture_message
import sentry_sdk.integrations.flask as flask_sentry

sentry = flask_sentry.FlaskSentry()
login_manager = LoginManager()

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.secret_key = 'haha'

    login_manager.init_app(app)

    sentry.init_app(app)

    @app.route('/message')
    def hi():
        capture_message('hi')
        return 'ok'

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


def test_flask_login_not_installed(app, capture_events, monkeypatch):
    monkeypatch.setattr(flask_sentry, 'current_user', None)

    client = app.test_client()
    client.get('/message')

    event, = capture_events
    assert event.get('user', {}).get('id') is None


def test_flask_login_not_configured(app, capture_events, monkeypatch):
    assert flask_sentry.current_user is not None
    client = app.test_client()
    client.get('/message')

    event, = capture_events
    assert event.get('user', {}).get('id') is None


def test_flask_login_partially_configured(app, capture_events, monkeypatch):
    login_manager = LoginManager()
    login_manager.init_app(app)

    client = app.test_client()
    client.get('/message')

    event, = capture_events
    assert event.get('user', {}).get('id') is None


@pytest.mark.parametrize('user_id', [None, '42', 3])
def test_flask_login_configured(app, user_id, capture_events, monkeypatch):
    class User(object):
        is_authenticated = is_active = True
        is_anonymous = user_id is not None

        def get_id(self):
            return str(user_id)

    @login_manager.user_loader
    def load_user(user_id):
        if user_id is not None:
            return User()

    @app.route('/login')
    def login():
        login_user(User())
        return 'ok'

    client = app.test_client()
    assert client.get('/login').status_code == 200
    assert not capture_events

    assert client.get('/message').status_code == 200

    event, = capture_events
    if user_id is None:
        assert event.get('user', {}).get('id') is None
    else:
        assert event['user']['id'] == str(user_id)
