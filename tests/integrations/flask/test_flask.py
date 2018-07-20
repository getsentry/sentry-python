import json
import pytest

flask = pytest.importorskip("flask")

from flask import Flask

from flask_login import LoginManager, login_user

from sentry_sdk import configure_scope, capture_message
import sentry_sdk.integrations.flask as flask_sentry

sentry = flask_sentry.FlaskSentry()
login_manager = LoginManager()


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "haha"

    login_manager.init_app(app)

    sentry.init_app(app)

    @app.route("/message")
    def hi():
        capture_message("hi")
        return "ok"

    return app


def test_has_context(app):
    @app.route("/")
    def index():
        with configure_scope() as scope:
            assert scope._data["transaction"] == "index"
            assert scope._data["request"]["data"] == ""
            assert scope._data["request"]["url"] == "http://localhost/"

        return "ok"

    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200


@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("testing", (True, False))
def test_errors(capture_exceptions, app, debug, testing):
    app.debug = debug
    app.testing = testing

    @app.route("/")
    def index():
        1 / 0

    client = app.test_client()
    try:
        client.get("/")
    except ZeroDivisionError:
        pass

    exc, = capture_exceptions
    assert isinstance(exc, ZeroDivisionError)


def test_flask_login_not_installed(app, capture_events, monkeypatch):
    monkeypatch.setattr(flask_sentry, "current_user", None)

    client = app.test_client()
    client.get("/message")

    event, = capture_events
    assert event.get("user", {}).get("id") is None


def test_flask_login_not_configured(app, capture_events, monkeypatch):
    assert flask_sentry.current_user is not None
    client = app.test_client()
    client.get("/message")

    event, = capture_events
    assert event.get("user", {}).get("id") is None


def test_flask_login_partially_configured(app, capture_events, monkeypatch):
    login_manager = LoginManager()
    login_manager.init_app(app)

    client = app.test_client()
    client.get("/message")

    event, = capture_events
    assert event.get("user", {}).get("id") is None


@pytest.mark.parametrize("user_id", [None, "42", 3])
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

    @app.route("/login")
    def login():
        if user_id is not None:
            login_user(User())
        return "ok"

    client = app.test_client()
    assert client.get("/login").status_code == 200
    assert not capture_events

    assert client.get("/message").status_code == 200

    event, = capture_events
    if user_id is None:
        assert event.get("user", {}).get("id") is None
    else:
        assert event["user"]["id"] == str(user_id)


def test_flask_large_json_request(capture_events, app):
    data = {'foo': {'bar': 'a' * 2000}}
    @app.route('/', methods=['POST'])
    def index():
        assert request.json == data
        assert request.data == json.dumps(data).encode('ascii')
        assert not request.form
        capture_message('hi')
        return 'ok'

    client = app.test_client()
    response = client.post(
        '/',
        content_type='application/json',
        data=json.dumps(data)
    )
    assert response.status_code == 200

    event, = capture_events
    assert event['']['request']['data']['foo']['bar'] == {
        '': {'len': 2000, 'rem': [['!len', 'x', 509, 512]]}
    }
    assert len(event['request']['data']['foo']['bar']) == 512
    assert event['request']['data_info'] == {
        'ct': 'json', 'repr': 'structured'
    }


def test_flask_large_formdata_request(capture_events, app):
    data = {'foo': 'a' * 2000}
    @app.route('/', methods=['POST'])
    def index():
        assert request.form['foo'] == data['foo']
        assert not request.data
        assert not request.json
        capture_message('hi')
        return 'ok'

    client = app.test_client()
    response = client.post(
        '/',
        data=data
    )
    assert response.status_code == 200

    event, = capture_events
    assert event['']['request']['data']['foo'] == {
        '': {'len': 2000, 'rem': [['!len', 'x', 509, 512]]}
    }
    assert len(event['request']['data']['foo']) == 512
    assert event['request']['data_info'] == {
        'ct': 'urlencoded', 'repr': 'structured'
    }


@pytest.mark.parametrize('input_char', [u'a', b'a'])
def test_flask_large_text_request(input_char, capture_events, app):
    data = input_char * 2000
    @app.route('/', methods=['POST'])
    def index():
        assert not request.form
        if isinstance(data, bytes):
            assert request.data == data
        else:
            assert request.data == data.encode('ascii')
        assert not request.json
        capture_message('hi')
        return 'ok'

    client = app.test_client()
    response = client.post(
        '/',
        data=data
    )
    assert response.status_code == 200

    event, = capture_events
    assert event['']['request']['data'] == {
        '': {'len': 2000, 'rem': [['!len', 'x', 509, 512]]}
    }
    assert len(event['request']['data']) == 512
    assert event['request']['data_info'] == {
        'ct': 'unicode', 'repr': 'text'
    }



def test_flask_large_bytes_request(capture_events, app):
    data = b'\xc3' * 2000
    @app.route('/', methods=['POST'])
    def index():
        assert not request.form
        assert request.data == data
        assert not request.json
        capture_message('hi')
        return 'ok'

    client = app.test_client()
    response = client.post(
        '/',
        data=data
    )
    assert response.status_code == 200

    event, = capture_events
    assert event['']['request']['data'] == {
        '': {'len': 2000, 'rem': [['!len', 'x', 509, 512]]}
    }
    assert len(event['request']['data']) == 512
    assert event['request']['data_info'] == {
        'ct': 'bytes', 'repr': 'base64'
    }
