import json
import pytest

from io import BytesIO

flask = pytest.importorskip("flask")

from flask import Flask, Response, request, abort, stream_with_context

from flask_login import LoginManager, login_user

from sentry_sdk import (
    configure_scope,
    capture_message,
    capture_exception,
    last_event_id,
)
from sentry_sdk.integrations.logging import LoggingIntegration
import sentry_sdk.integrations.flask as flask_sentry


login_manager = LoginManager()


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "haha"

    login_manager.init_app(app)

    @app.route("/message")
    def hi():
        capture_message("hi")
        return "ok"

    return app


def test_has_context(sentry_init, app, capture_events):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])
    events = capture_events()

    client = app.test_client()
    response = client.get("/message")
    assert response.status_code == 200

    event, = events
    assert event["transaction"] == "hi"
    assert "data" not in event["request"]
    assert event["request"]["url"] == "http://localhost/message"


@pytest.mark.parametrize(
    "transaction_style,expected_transaction", [("endpoint", "hi"), ("url", "/message")]
)
def test_transaction_style(
    sentry_init, app, capture_events, transaction_style, expected_transaction
):
    sentry_init(
        integrations=[
            flask_sentry.FlaskIntegration(transaction_style=transaction_style)
        ]
    )
    events = capture_events()

    client = app.test_client()
    response = client.get("/message")
    assert response.status_code == 200

    event, = events
    assert event["transaction"] == expected_transaction


@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("testing", (True, False))
def test_errors(sentry_init, capture_exceptions, capture_events, app, debug, testing):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()], debug=True)

    app.debug = debug
    app.testing = testing

    @app.route("/")
    def index():
        1 / 0

    exceptions = capture_exceptions()
    events = capture_events()

    client = app.test_client()
    try:
        client.get("/")
    except ZeroDivisionError:
        pass

    exc, = exceptions
    assert isinstance(exc, ZeroDivisionError)

    event, = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "flask"


def test_flask_login_not_installed(sentry_init, app, capture_events, monkeypatch):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    monkeypatch.setattr(flask_sentry, "flask_login", None)

    events = capture_events()

    client = app.test_client()
    client.get("/message")

    event, = events
    assert event.get("user", {}).get("id") is None


def test_flask_login_not_configured(sentry_init, app, capture_events, monkeypatch):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    assert flask_sentry.flask_login

    events = capture_events()
    client = app.test_client()
    client.get("/message")

    event, = events
    assert event.get("user", {}).get("id") is None


def test_flask_login_partially_configured(
    sentry_init, app, capture_events, monkeypatch
):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    events = capture_events()

    login_manager = LoginManager()
    login_manager.init_app(app)

    client = app.test_client()
    client.get("/message")

    event, = events
    assert event.get("user", {}).get("id") is None


@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("user_id", [None, "42", 3])
def test_flask_login_configured(
    send_default_pii, sentry_init, app, user_id, capture_events, monkeypatch
):
    sentry_init(
        send_default_pii=send_default_pii,
        integrations=[flask_sentry.FlaskIntegration()],
    )

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

    events = capture_events()

    client = app.test_client()
    assert client.get("/login").status_code == 200
    assert not events

    assert client.get("/message").status_code == 200

    event, = events
    if user_id is None or not send_default_pii:
        assert event.get("user", {}).get("id") is None
    else:
        assert event["user"]["id"] == str(user_id)


def test_flask_large_json_request(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    data = {"foo": {"bar": "a" * 2000}}

    @app.route("/", methods=["POST"])
    def index():
        assert request.json == data
        assert request.data == json.dumps(data).encode("ascii")
        assert not request.form
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", content_type="application/json", data=json.dumps(data))
    assert response.status_code == 200

    event, = events
    assert event["_meta"]["request"]["data"]["foo"]["bar"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]["bar"]) == 512


def test_flask_medium_formdata_request(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    data = {"foo": "a" * 2000}

    @app.route("/", methods=["POST"])
    def index():
        assert request.form["foo"] == data["foo"]
        assert not request.data
        assert not request.json
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    event, = events
    assert event["_meta"]["request"]["data"]["foo"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]) == 512


@pytest.mark.parametrize("input_char", [u"a", b"a"])
def test_flask_too_large_raw_request(sentry_init, input_char, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()], request_bodies="small")

    data = input_char * 2000

    @app.route("/", methods=["POST"])
    def index():
        assert not request.form
        if isinstance(data, bytes):
            assert request.data == data
        else:
            assert request.data == data.encode("ascii")
        assert not request.json
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    event, = events
    assert event["_meta"]["request"]["data"] == {
        "": {"len": 2000, "rem": [["!config", "x", 0, 2000]]}
    }
    assert not event["request"]["data"]


def test_flask_files_and_form(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()], request_bodies="always")

    data = {"foo": "a" * 2000, "file": (BytesIO(b"hello"), "hello.txt")}

    @app.route("/", methods=["POST"])
    def index():
        assert list(request.form) == ["foo"]
        assert list(request.files) == ["file"]
        assert not request.json
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    event, = events
    assert event["_meta"]["request"]["data"]["foo"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]) == 512

    assert event["_meta"]["request"]["data"]["file"] == {
        "": {"len": 0, "rem": [["!raw", "x", 0, 0]]}
    }
    assert not event["request"]["data"]["file"]


@pytest.mark.parametrize(
    "integrations",
    [
        [flask_sentry.FlaskIntegration()],
        [flask_sentry.FlaskIntegration(), LoggingIntegration(event_level="ERROR")],
    ],
)
def test_errors_not_reported_twice(sentry_init, integrations, capture_events, app):
    sentry_init(integrations=integrations)

    @app.route("/")
    def index():
        try:
            1 / 0
        except Exception as e:
            app.logger.exception(e)
            raise e

    events = capture_events()

    client = app.test_client()
    with pytest.raises(ZeroDivisionError):
        client.get("/")

    assert len(events) == 1


def test_logging(sentry_init, capture_events, app):
    # ensure that Flask's logger magic doesn't break ours
    sentry_init(
        integrations=[
            flask_sentry.FlaskIntegration(),
            LoggingIntegration(event_level="ERROR"),
        ]
    )

    @app.route("/")
    def index():
        app.logger.error("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    client.get("/")

    event, = events
    assert event["level"] == "error"


def test_no_errors_without_request(app, sentry_init):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])
    with app.app_context():
        capture_exception(ValueError())


def test_cli_commands_raise(app):
    if not hasattr(app, "cli"):
        pytest.skip("Too old flask version")

    from flask.cli import ScriptInfo

    @app.cli.command()
    def foo():
        1 / 0

    with pytest.raises(ZeroDivisionError):
        app.cli.main(
            args=["foo"], prog_name="myapp", obj=ScriptInfo(create_app=lambda _: app)
        )


def test_wsgi_level_error_is_caught(
    app, capture_exceptions, capture_events, sentry_init
):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    def wsgi_app(environ, start_response):
        1 / 0

    app.wsgi_app = wsgi_app

    client = app.test_client()

    exceptions = capture_exceptions()
    events = capture_events()

    with pytest.raises(ZeroDivisionError) as exc:
        client.get("/")

    error, = exceptions

    assert error is exc.value

    event, = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "wsgi"


def test_500(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    app.debug = False
    app.testing = False

    @app.route("/")
    def index():
        1 / 0

    @app.errorhandler(500)
    def error_handler(err):
        return "Sentry error: %s" % last_event_id()

    events = capture_events()

    client = app.test_client()
    response = client.get("/")

    event, = events
    assert response.data.decode("utf-8") == "Sentry error: %s" % event["event_id"]


def test_error_in_errorhandler(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    app.debug = False
    app.testing = False

    @app.route("/")
    def index():
        raise ValueError()

    @app.errorhandler(500)
    def error_handler(err):
        1 / 0

    events = capture_events()

    client = app.test_client()

    with pytest.raises(ZeroDivisionError):
        client.get("/")

    event1, event2 = events

    exception, = event1["exception"]["values"]
    assert exception["type"] == "ValueError"

    exception = event2["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


def test_bad_request_not_captured(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])
    events = capture_events()

    @app.route("/")
    def index():
        abort(400)

    client = app.test_client()

    client.get("/")

    assert not events


def test_does_not_leak_scope(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])
    events = capture_events()

    with configure_scope() as scope:
        scope.set_tag("request_data", False)

    @app.route("/")
    def index():
        with configure_scope() as scope:
            scope.set_tag("request_data", True)

        def generate():
            for row in range(1000):
                with configure_scope() as scope:
                    assert scope._tags["request_data"]

                yield str(row) + "\n"

        return Response(stream_with_context(generate()), mimetype="text/csv")

    client = app.test_client()
    response = client.get("/")
    assert response.data.decode() == "".join(str(row) + "\n" for row in range(1000))
    assert not events

    with configure_scope() as scope:
        assert not scope._tags["request_data"]


def test_scoped_test_client(sentry_init, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    @app.route("/")
    def index():
        return "ok"

    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200
