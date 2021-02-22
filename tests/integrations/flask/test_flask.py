import json
import pytest
import logging

from io import BytesIO

flask = pytest.importorskip("flask")

from flask import Flask, Response, request, abort, stream_with_context
from flask.views import View

from flask_login import LoginManager, login_user

from sentry_sdk import (
    set_tag,
    configure_scope,
    capture_message,
    capture_exception,
    last_event_id,
    Hub,
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


@pytest.fixture(params=("auto", "manual"))
def integration_enabled_params(request):
    if request.param == "auto":
        return {"auto_enabling_integrations": True}
    elif request.param == "manual":
        return {"integrations": [flask_sentry.FlaskIntegration()]}
    else:
        raise ValueError(request.param)


def test_has_context(sentry_init, app, capture_events):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])
    events = capture_events()

    client = app.test_client()
    response = client.get("/message")
    assert response.status_code == 200

    (event,) = events
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

    (event,) = events
    assert event["transaction"] == expected_transaction


@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("testing", (True, False))
def test_errors(
    sentry_init,
    capture_exceptions,
    capture_events,
    app,
    debug,
    testing,
    integration_enabled_params,
):
    sentry_init(debug=True, **integration_enabled_params)

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

    (exc,) = exceptions
    assert isinstance(exc, ZeroDivisionError)

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "flask"


def test_flask_login_not_installed(
    sentry_init, app, capture_events, monkeypatch, integration_enabled_params
):
    sentry_init(**integration_enabled_params)

    monkeypatch.setattr(flask_sentry, "flask_login", None)

    events = capture_events()

    client = app.test_client()
    client.get("/message")

    (event,) = events
    assert event.get("user", {}).get("id") is None


def test_flask_login_not_configured(
    sentry_init, app, capture_events, monkeypatch, integration_enabled_params
):
    sentry_init(**integration_enabled_params)

    assert flask_sentry.flask_login

    events = capture_events()
    client = app.test_client()
    client.get("/message")

    (event,) = events
    assert event.get("user", {}).get("id") is None


def test_flask_login_partially_configured(
    sentry_init, app, capture_events, monkeypatch, integration_enabled_params
):
    sentry_init(**integration_enabled_params)

    events = capture_events()

    login_manager = LoginManager()
    login_manager.init_app(app)

    client = app.test_client()
    client.get("/message")

    (event,) = events
    assert event.get("user", {}).get("id") is None


@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("user_id", [None, "42", 3])
def test_flask_login_configured(
    send_default_pii,
    sentry_init,
    app,
    user_id,
    capture_events,
    monkeypatch,
    integration_enabled_params,
):
    sentry_init(send_default_pii=send_default_pii, **integration_enabled_params)

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

    (event,) = events
    if user_id is None or not send_default_pii:
        assert event.get("user", {}).get("id") is None
    else:
        assert event["user"]["id"] == str(user_id)


def test_flask_large_json_request(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    data = {"foo": {"bar": "a" * 2000}}

    @app.route("/", methods=["POST"])
    def index():
        assert request.get_json() == data
        assert request.get_data() == json.dumps(data).encode("ascii")
        assert not request.form
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", content_type="application/json", data=json.dumps(data))
    assert response.status_code == 200

    (event,) = events
    assert event["_meta"]["request"]["data"]["foo"]["bar"] == {
        "": {"len": 2000, "rem": [["!limit", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]["bar"]) == 512


def test_flask_session_tracking(sentry_init, capture_envelopes, app):
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()],
        release="demo-release",
        _experiments=dict(
            auto_session_tracking=True,
        ),
    )

    @app.route("/")
    def index():
        with configure_scope() as scope:
            scope.set_user({"ip_address": "1.2.3.4", "id": "42"})
        try:
            raise ValueError("stuff")
        except Exception:
            logging.exception("stuff happened")
        1 / 0

    envelopes = capture_envelopes()

    with app.test_client() as client:
        try:
            client.get("/", headers={"User-Agent": "blafasel/1.0"})
        except ZeroDivisionError:
            pass

    Hub.current.client.flush()

    (first_event, error_event, session) = envelopes
    first_event = first_event.get_event()
    error_event = error_event.get_event()
    session = session.items[0].payload.json
    aggregates = session["aggregates"]

    assert first_event["exception"]["values"][0]["type"] == "ValueError"
    assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"

    assert len(aggregates) == 1
    assert aggregates[0]["crashed"] == 1
    assert aggregates[0]["started"]
    assert session["attrs"]["release"] == "demo-release"
    assert session["attrs"]["ip_address"] == "1.2.3.4"
    assert session["attrs"]["user_agent"] == "blafasel/1.0"


@pytest.mark.parametrize("data", [{}, []], ids=["empty-dict", "empty-list"])
def test_flask_empty_json_request(sentry_init, capture_events, app, data):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    @app.route("/", methods=["POST"])
    def index():
        assert request.get_json() == data
        assert request.get_data() == json.dumps(data).encode("ascii")
        assert not request.form
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", content_type="application/json", data=json.dumps(data))
    assert response.status_code == 200

    (event,) = events
    assert event["request"]["data"] == data


def test_flask_medium_formdata_request(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    data = {"foo": "a" * 2000}

    @app.route("/", methods=["POST"])
    def index():
        assert request.form["foo"] == data["foo"]
        assert not request.get_data()
        assert not request.get_json()
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    (event,) = events
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
            assert request.get_data() == data
        else:
            assert request.get_data() == data.encode("ascii")
        assert not request.get_json()
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    (event,) = events
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
        assert not request.get_json()
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    (event,) = events
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

    (event,) = events
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

    (error,) = exceptions

    assert error is exc.value

    (event,) = events
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

    (event,) = events
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

    (exception,) = event1["exception"]["values"]
    assert exception["type"] == "ValueError"

    exception = event2["exception"]["values"][-1]
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


@pytest.mark.parametrize("exc_cls", [ZeroDivisionError, Exception])
def test_errorhandler_for_exception_swallows_exception(
    sentry_init, app, capture_events, exc_cls
):
    # In contrast to error handlers for a status code, error
    # handlers for exceptions can swallow the exception (this is
    # just how the Flask signal works)
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])
    events = capture_events()

    @app.route("/")
    def index():
        1 / 0

    @app.errorhandler(exc_cls)
    def zerodivision(e):
        return "ok"

    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200

    assert not events


def test_tracing_success(sentry_init, capture_events, app):
    sentry_init(traces_sample_rate=1.0, integrations=[flask_sentry.FlaskIntegration()])

    @app.before_request
    def _():
        set_tag("before_request", "yes")

    @app.route("/message_tx")
    def hi_tx():
        set_tag("view", "yes")
        capture_message("hi")
        return "ok"

    events = capture_events()

    with app.test_client() as client:
        response = client.get("/message_tx")
        assert response.status_code == 200

    message_event, transaction_event = events

    assert transaction_event["type"] == "transaction"
    assert transaction_event["transaction"] == "hi_tx"
    assert transaction_event["contexts"]["trace"]["status"] == "ok"
    assert transaction_event["tags"]["view"] == "yes"
    assert transaction_event["tags"]["before_request"] == "yes"

    assert message_event["message"] == "hi"
    assert message_event["transaction"] == "hi_tx"
    assert message_event["tags"]["view"] == "yes"
    assert message_event["tags"]["before_request"] == "yes"


def test_tracing_error(sentry_init, capture_events, app):
    sentry_init(traces_sample_rate=1.0, integrations=[flask_sentry.FlaskIntegration()])

    events = capture_events()

    @app.route("/error")
    def error():
        1 / 0

    with pytest.raises(ZeroDivisionError):
        with app.test_client() as client:
            response = client.get("/error")
            assert response.status_code == 500

    error_event, transaction_event = events

    assert transaction_event["type"] == "transaction"
    assert transaction_event["transaction"] == "error"
    assert transaction_event["contexts"]["trace"]["status"] == "internal_error"

    assert error_event["transaction"] == "error"
    (exception,) = error_event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"


def test_class_based_views(sentry_init, app, capture_events):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])
    events = capture_events()

    @app.route("/")
    class HelloClass(View):
        def dispatch_request(self):
            capture_message("hi")
            return "ok"

    app.add_url_rule("/hello-class/", view_func=HelloClass.as_view("hello_class"))

    with app.test_client() as client:
        response = client.get("/hello-class/")
        assert response.status_code == 200

    (event,) = events

    assert event["message"] == "hi"
    assert event["transaction"] == "hello_class"
