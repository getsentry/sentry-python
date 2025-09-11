import json
import re
import logging
from io import BytesIO

import pytest
from flask import (
    Flask,
    Response,
    request,
    abort,
    stream_with_context,
    render_template_string,
)
from flask.views import View
from flask_login import LoginManager, login_user

try:
    from werkzeug.wrappers.request import UnsupportedMediaType
except ImportError:
    UnsupportedMediaType = None

import sentry_sdk
import sentry_sdk.integrations.flask as flask_sentry
from sentry_sdk import (
    set_tag,
    capture_message,
    capture_exception,
)
from sentry_sdk.consts import DEFAULT_MAX_VALUE_LENGTH
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.serializer import MAX_DATABAG_BREADTH


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

    @app.route("/nomessage")
    def nohi():
        return "ok"

    @app.route("/message/<int:message_id>")
    def hi_with_id(message_id):
        capture_message("hi again")
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
    "url,transaction_style,expected_transaction,expected_source",
    [
        ("/message", "endpoint", "hi", "component"),
        ("/message", "url", "/message", "route"),
        ("/message/123456", "endpoint", "hi_with_id", "component"),
        ("/message/123456", "url", "/message/<int:message_id>", "route"),
    ],
)
def test_transaction_style(
    sentry_init,
    app,
    capture_events,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
):
    sentry_init(
        integrations=[
            flask_sentry.FlaskIntegration(transaction_style=transaction_style)
        ]
    )
    events = capture_events()

    client = app.test_client()
    response = client.get(url)
    assert response.status_code == 200

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


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
    sentry_init(**integration_enabled_params)

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

    class User:
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
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()], max_request_body_size="always"
    )

    data = {"foo": {"bar": "a" * (DEFAULT_MAX_VALUE_LENGTH + 10)}}

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
        "": {
            "len": DEFAULT_MAX_VALUE_LENGTH + 10,
            "rem": [
                ["!limit", "x", DEFAULT_MAX_VALUE_LENGTH - 3, DEFAULT_MAX_VALUE_LENGTH]
            ],
        }
    }
    assert len(event["request"]["data"]["foo"]["bar"]) == DEFAULT_MAX_VALUE_LENGTH


def test_flask_session_tracking(sentry_init, capture_envelopes, app):
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()],
        release="demo-release",
    )

    @app.route("/")
    def index():
        sentry_sdk.get_isolation_scope().set_user({"ip_address": "1.2.3.4", "id": "42"})
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

    sentry_sdk.get_client().flush()

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
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()], max_request_body_size="always"
    )

    data = {"foo": "a" * (DEFAULT_MAX_VALUE_LENGTH + 10)}

    @app.route("/", methods=["POST"])
    def index():
        assert request.form["foo"] == data["foo"]
        assert not request.get_data()
        try:
            assert not request.get_json()
        except UnsupportedMediaType:
            # flask/werkzeug 3
            pass
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    (event,) = events
    assert event["_meta"]["request"]["data"]["foo"] == {
        "": {
            "len": DEFAULT_MAX_VALUE_LENGTH + 10,
            "rem": [
                ["!limit", "x", DEFAULT_MAX_VALUE_LENGTH - 3, DEFAULT_MAX_VALUE_LENGTH]
            ],
        }
    }
    assert len(event["request"]["data"]["foo"]) == DEFAULT_MAX_VALUE_LENGTH


def test_flask_formdata_request_appear_transaction_body(
    sentry_init, capture_events, app
):
    """
    Test that ensures that transaction request data contains body, even if no exception was raised
    """
    sentry_init(integrations=[flask_sentry.FlaskIntegration()], traces_sample_rate=1.0)

    data = {"username": "sentry-user", "age": "26"}

    @app.route("/", methods=["POST"])
    def index():
        assert request.form["username"] == data["username"]
        assert request.form["age"] == data["age"]
        assert not request.get_data()
        try:
            assert not request.get_json()
        except UnsupportedMediaType:
            # flask/werkzeug 3
            pass
        set_tag("view", "yes")
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    event, transaction_event = events

    assert "request" in transaction_event
    assert "data" in transaction_event["request"]
    assert transaction_event["request"]["data"] == data


@pytest.mark.parametrize("input_char", ["a", b"a"])
def test_flask_too_large_raw_request(sentry_init, input_char, capture_events, app):
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()], max_request_body_size="small"
    )

    data = input_char * 2000

    @app.route("/", methods=["POST"])
    def index():
        assert not request.form
        if isinstance(data, bytes):
            assert request.get_data() == data
        else:
            assert request.get_data() == data.encode("ascii")
        try:
            assert not request.get_json()
        except UnsupportedMediaType:
            # flask/werkzeug 3
            pass
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    (event,) = events
    assert event["_meta"]["request"]["data"] == {"": {"rem": [["!config", "x"]]}}
    assert not event["request"]["data"]


def test_flask_files_and_form(sentry_init, capture_events, app):
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()], max_request_body_size="always"
    )

    data = {
        "foo": "a" * (DEFAULT_MAX_VALUE_LENGTH + 10),
        "file": (BytesIO(b"hello"), "hello.txt"),
    }

    @app.route("/", methods=["POST"])
    def index():
        assert list(request.form) == ["foo"]
        assert list(request.files) == ["file"]
        try:
            assert not request.get_json()
        except UnsupportedMediaType:
            # flask/werkzeug 3
            pass
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    (event,) = events
    assert event["_meta"]["request"]["data"]["foo"] == {
        "": {
            "len": DEFAULT_MAX_VALUE_LENGTH + 10,
            "rem": [
                ["!limit", "x", DEFAULT_MAX_VALUE_LENGTH - 3, DEFAULT_MAX_VALUE_LENGTH]
            ],
        }
    }
    assert len(event["request"]["data"]["foo"]) == DEFAULT_MAX_VALUE_LENGTH

    assert event["_meta"]["request"]["data"]["file"] == {"": {"rem": [["!raw", "x"]]}}
    assert not event["request"]["data"]["file"]


def test_json_not_truncated_if_max_request_body_size_is_always(
    sentry_init, capture_events, app
):
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()], max_request_body_size="always"
    )

    data = {
        "key{}".format(i): "value{}".format(i) for i in range(MAX_DATABAG_BREADTH + 10)
    }

    @app.route("/", methods=["POST"])
    def index():
        assert request.get_json() == data
        assert request.get_data() == json.dumps(data).encode("ascii")
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", content_type="application/json", data=json.dumps(data))
    assert response.status_code == 200

    (event,) = events
    assert event["request"]["data"] == data


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

    def create_app(*_):
        return app

    with pytest.raises(ZeroDivisionError):
        app.cli.main(
            args=["foo"], prog_name="myapp", obj=ScriptInfo(create_app=create_app)
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


def test_500(sentry_init, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    app.debug = False
    app.testing = False

    @app.route("/")
    def index():
        1 / 0

    @app.errorhandler(500)
    def error_handler(err):
        return "Sentry error."

    client = app.test_client()
    response = client.get("/")

    assert response.data.decode("utf-8") == "Sentry error."


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

    sentry_sdk.get_isolation_scope().set_tag("request_data", False)

    @app.route("/")
    def index():
        sentry_sdk.get_isolation_scope().set_tag("request_data", True)

        def generate():
            for row in range(1000):
                assert sentry_sdk.get_isolation_scope()._tags["request_data"]

                yield str(row) + "\n"

        return Response(stream_with_context(generate()), mimetype="text/csv")

    client = app.test_client()
    response = client.get("/")
    assert response.data.decode() == "".join(str(row) + "\n" for row in range(1000))
    assert not events

    assert not sentry_sdk.get_isolation_scope()._tags["request_data"]


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


def test_error_has_trace_context_if_tracing_disabled(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    events = capture_events()

    @app.route("/error")
    def error():
        1 / 0

    with pytest.raises(ZeroDivisionError):
        with app.test_client() as client:
            response = client.get("/error")
            assert response.status_code == 500

    (error_event,) = events

    assert error_event["contexts"]["trace"]


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


@pytest.mark.parametrize(
    "template_string", ["{{ sentry_trace }}", "{{ sentry_trace_meta }}"]
)
def test_template_tracing_meta(sentry_init, app, capture_events, template_string):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])
    events = capture_events()

    @app.route("/")
    def index():
        capture_message(sentry_sdk.get_traceparent() + "\n" + sentry_sdk.get_baggage())
        return render_template_string(template_string)

    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200

        rendered_meta = response.data.decode("utf-8")
        traceparent, baggage = events[0]["message"].split("\n")
        assert traceparent != ""
        assert baggage != ""

    match = re.match(
        r'^<meta name="sentry-trace" content="([^\"]*)"><meta name="baggage" content="([^\"]*)">',
        rendered_meta,
    )
    assert match is not None
    assert match.group(1) == traceparent

    rendered_baggage = match.group(2)
    assert rendered_baggage == baggage


def test_dont_override_sentry_trace_context(sentry_init, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    @app.route("/")
    def index():
        return render_template_string("{{ sentry_trace }}", sentry_trace="hi")

    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.data == b"hi"


def test_request_not_modified_by_reference(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    @app.route("/", methods=["POST"])
    def index():
        logging.critical("oops")
        assert request.get_json() == {"password": "ohno"}
        assert request.headers["Authorization"] == "Bearer ohno"
        return "ok"

    events = capture_events()

    client = app.test_client()
    client.post(
        "/", json={"password": "ohno"}, headers={"Authorization": "Bearer ohno"}
    )

    (event,) = events

    assert event["request"]["data"]["password"] == "[Filtered]"
    assert event["request"]["headers"]["Authorization"] == "[Filtered]"


def test_response_status_code_ok_in_transaction_context(
    sentry_init, capture_envelopes, app
):
    """
    Tests that the response status code is added to the transaction context.
    This also works for when there is an Exception during the request, but somehow the test flask app doesn't seem to trigger that.
    """
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()],
        traces_sample_rate=1.0,
        release="demo-release",
    )

    envelopes = capture_envelopes()

    client = app.test_client()
    client.get("/message")

    sentry_sdk.get_client().flush()

    (_, transaction_envelope, _) = envelopes
    transaction = transaction_envelope.get_transaction_event()

    assert transaction["type"] == "transaction"
    assert len(transaction["contexts"]) > 0
    assert (
        "response" in transaction["contexts"].keys()
    ), "Response context not found in transaction"
    assert transaction["contexts"]["response"]["status_code"] == 200


def test_response_status_code_not_found_in_transaction_context(
    sentry_init, capture_envelopes, app
):
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()],
        traces_sample_rate=1.0,
        release="demo-release",
    )

    envelopes = capture_envelopes()

    client = app.test_client()
    client.get("/not-existing-route")

    sentry_sdk.get_client().flush()

    (transaction_envelope, _) = envelopes
    transaction = transaction_envelope.get_transaction_event()

    assert transaction["type"] == "transaction"
    assert len(transaction["contexts"]) > 0
    assert (
        "response" in transaction["contexts"].keys()
    ), "Response context not found in transaction"
    assert transaction["contexts"]["response"]["status_code"] == 404


def test_span_origin(sentry_init, app, capture_events):
    sentry_init(
        integrations=[flask_sentry.FlaskIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = app.test_client()
    client.get("/message")

    (_, event) = events

    assert event["contexts"]["trace"]["origin"] == "auto.http.flask"


def test_transaction_http_method_default(
    sentry_init,
    app,
    capture_events,
):
    """
    By default OPTIONS and HEAD requests do not create a transaction.
    """
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[flask_sentry.FlaskIntegration()],
    )
    events = capture_events()

    client = app.test_client()
    response = client.get("/nomessage")
    assert response.status_code == 200

    response = client.options("/nomessage")
    assert response.status_code == 200

    response = client.head("/nomessage")
    assert response.status_code == 200

    (event,) = events

    assert len(events) == 1
    assert event["request"]["method"] == "GET"


def test_transaction_http_method_custom(
    sentry_init,
    app,
    capture_events,
):
    """
    Configure FlaskIntegration to ONLY capture OPTIONS and HEAD requests.
    """
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[
            flask_sentry.FlaskIntegration(
                http_methods_to_capture=(
                    "OPTIONS",
                    "head",
                )  # capitalization does not matter
            )  # case does not matter
        ],
    )
    events = capture_events()

    client = app.test_client()
    response = client.get("/nomessage")
    assert response.status_code == 200

    response = client.options("/nomessage")
    assert response.status_code == 200

    response = client.head("/nomessage")
    assert response.status_code == 200

    assert len(events) == 2

    (event1, event2) = events
    assert event1["request"]["method"] == "OPTIONS"
    assert event2["request"]["method"] == "HEAD"
