import json
import threading
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk import (
    set_tag,
    capture_message,
    capture_exception,
)
from sentry_sdk.integrations.logging import LoggingIntegration
import sentry_sdk.integrations.quart as quart_sentry


def quart_app_factory():
    # These imports are inlined because the `test_quart_flask_patch` testcase
    # tests behavior that is triggered by importing a package before any Quart
    # imports happen
    from quart import Quart

    try:
        from quart_auth import QuartAuth

        auth_manager = QuartAuth()
    except ImportError:
        from quart_auth import AuthManager

        auth_manager = AuthManager()

    app = Quart(__name__)
    app.debug = False
    app.config["TESTING"] = False
    app.secret_key = "haha"

    auth_manager.init_app(app)

    @app.route("/message")
    async def hi():
        capture_message("hi")
        return "ok"

    @app.route("/message/<message_id>")
    async def hi_with_id(message_id):
        capture_message("hi with id")
        return "ok with id"

    @app.get("/sync/thread_ids")
    def _thread_ids_sync():
        return {
            "main": str(threading.main_thread().ident),
            "active": str(threading.current_thread().ident),
        }

    @app.get("/async/thread_ids")
    async def _thread_ids_async():
        return {
            "main": str(threading.main_thread().ident),
            "active": str(threading.current_thread().ident),
        }

    return app


@pytest.fixture(params=("manual",))
def integration_enabled_params(request):
    if request.param == "manual":
        return {"integrations": [quart_sentry.QuartIntegration()]}
    else:
        raise ValueError(request.param)


@pytest.mark.asyncio
@pytest.mark.forked
async def test_quart_flask_patch(sentry_init, capture_events, reset_integrations):
    # This testcase is forked because `import quart_flask_patch` needs to run
    # before anything else Quart-related is imported (since it monkeypatches
    # some things) and we don't want this to affect other testcases.
    #
    # It's also important this testcase be run before any other testcase
    # that uses `quart_app_factory`.
    import quart_flask_patch  # noqa: F401

    app = quart_app_factory()
    sentry_init(
        integrations=[quart_sentry.QuartIntegration()],
    )

    @app.route("/")
    async def index():
        return "ok"

    events = capture_events()

    client = app.test_client()
    await client.get("/")

    assert not events


@pytest.mark.asyncio
async def test_has_context(sentry_init, capture_events):
    sentry_init(integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()
    events = capture_events()

    client = app.test_client()
    response = await client.get("/message")
    assert response.status_code == 200

    (event,) = events
    assert event["transaction"] == "hi"
    assert "data" not in event["request"]
    assert event["request"]["url"] == "http://localhost/message"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,transaction_style,expected_transaction,expected_source",
    [
        ("/message", "endpoint", "hi", "component"),
        ("/message", "url", "/message", "route"),
        ("/message/123456", "endpoint", "hi_with_id", "component"),
        ("/message/123456", "url", "/message/<message_id>", "route"),
    ],
)
async def test_transaction_style(
    sentry_init,
    capture_events,
    url,
    transaction_style,
    expected_transaction,
    expected_source,
):
    sentry_init(
        integrations=[
            quart_sentry.QuartIntegration(transaction_style=transaction_style)
        ]
    )
    app = quart_app_factory()
    events = capture_events()

    client = app.test_client()
    response = await client.get(url)
    assert response.status_code == 200

    (event,) = events
    assert event["transaction"] == expected_transaction


@pytest.mark.asyncio
async def test_errors(
    sentry_init,
    capture_exceptions,
    capture_events,
    integration_enabled_params,
):
    sentry_init(**integration_enabled_params)
    app = quart_app_factory()

    @app.route("/")
    async def index():
        1 / 0

    exceptions = capture_exceptions()
    events = capture_events()

    client = app.test_client()
    try:
        await client.get("/")
    except ZeroDivisionError:
        pass

    (exc,) = exceptions
    assert isinstance(exc, ZeroDivisionError)

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "quart"


@pytest.mark.asyncio
async def test_quart_auth_not_installed(
    sentry_init, capture_events, monkeypatch, integration_enabled_params
):
    sentry_init(**integration_enabled_params)
    app = quart_app_factory()

    monkeypatch.setattr(quart_sentry, "quart_auth", None)

    events = capture_events()

    client = app.test_client()
    await client.get("/message")

    (event,) = events
    assert event.get("user", {}).get("id") is None


@pytest.mark.asyncio
async def test_quart_auth_not_configured(
    sentry_init, capture_events, monkeypatch, integration_enabled_params
):
    sentry_init(**integration_enabled_params)
    app = quart_app_factory()

    assert quart_sentry.quart_auth

    events = capture_events()
    client = app.test_client()
    await client.get("/message")

    (event,) = events
    assert event.get("user", {}).get("id") is None


@pytest.mark.asyncio
async def test_quart_auth_partially_configured(
    sentry_init, capture_events, monkeypatch, integration_enabled_params
):
    sentry_init(**integration_enabled_params)
    app = quart_app_factory()

    events = capture_events()

    client = app.test_client()
    await client.get("/message")

    (event,) = events
    assert event.get("user", {}).get("id") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("user_id", [None, "42", "3"])
async def test_quart_auth_configured(
    send_default_pii,
    sentry_init,
    user_id,
    capture_events,
    monkeypatch,
    integration_enabled_params,
):
    from quart_auth import AuthUser, login_user

    sentry_init(send_default_pii=send_default_pii, **integration_enabled_params)
    app = quart_app_factory()

    @app.route("/login")
    async def login():
        if user_id is not None:
            login_user(AuthUser(user_id))
        return "ok"

    events = capture_events()

    client = app.test_client()
    assert (await client.get("/login")).status_code == 200
    assert not events

    assert (await client.get("/message")).status_code == 200

    (event,) = events
    if user_id is None or not send_default_pii:
        assert event.get("user", {}).get("id") is None
    else:
        assert event["user"]["id"] == str(user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "integrations",
    [
        [quart_sentry.QuartIntegration()],
        [quart_sentry.QuartIntegration(), LoggingIntegration(event_level="ERROR")],
    ],
)
async def test_errors_not_reported_twice(sentry_init, integrations, capture_events):
    sentry_init(integrations=integrations)
    app = quart_app_factory()

    @app.route("/")
    async def index():
        try:
            1 / 0
        except Exception as e:
            app.logger.exception(e)
            raise e

    events = capture_events()

    client = app.test_client()
    # with pytest.raises(ZeroDivisionError):
    await client.get("/")

    assert len(events) == 1


@pytest.mark.asyncio
async def test_logging(sentry_init, capture_events):
    # ensure that Quart's logger magic doesn't break ours
    sentry_init(
        integrations=[
            quart_sentry.QuartIntegration(),
            LoggingIntegration(event_level="ERROR"),
        ]
    )
    app = quart_app_factory()

    @app.route("/")
    async def index():
        app.logger.error("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    await client.get("/")

    (event,) = events
    assert event["level"] == "error"


@pytest.mark.asyncio
async def test_no_errors_without_request(sentry_init):
    sentry_init(integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()

    async with app.app_context():
        capture_exception(ValueError())


def test_cli_commands_raise():
    app = quart_app_factory()

    if not hasattr(app, "cli"):
        pytest.skip("Too old quart version")

    from quart.cli import ScriptInfo

    @app.cli.command()
    def foo():
        1 / 0

    with pytest.raises(ZeroDivisionError):
        app.cli.main(
            args=["foo"], prog_name="myapp", obj=ScriptInfo(create_app=lambda _: app)
        )


@pytest.mark.asyncio
async def test_500(sentry_init):
    sentry_init(integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()

    @app.route("/")
    async def index():
        1 / 0

    @app.errorhandler(500)
    async def error_handler(err):
        return "Sentry error."

    client = app.test_client()
    response = await client.get("/")

    assert (await response.get_data(as_text=True)) == "Sentry error."


@pytest.mark.asyncio
async def test_error_in_errorhandler(sentry_init, capture_events):
    sentry_init(integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()

    @app.route("/")
    async def index():
        raise ValueError()

    @app.errorhandler(500)
    async def error_handler(err):
        1 / 0

    events = capture_events()

    client = app.test_client()

    with pytest.raises(ZeroDivisionError):
        await client.get("/")

    event1, event2 = events

    (exception,) = event1["exception"]["values"]
    assert exception["type"] == "ValueError"

    exception = event2["exception"]["values"][-1]
    assert exception["type"] == "ZeroDivisionError"


@pytest.mark.asyncio
async def test_bad_request_not_captured(sentry_init, capture_events):
    from quart import abort

    sentry_init(integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()
    events = capture_events()

    @app.route("/")
    async def index():
        abort(400)

    client = app.test_client()

    await client.get("/")

    assert not events


@pytest.mark.asyncio
async def test_does_not_leak_scope(sentry_init, capture_events):
    from quart import Response, stream_with_context

    sentry_init(integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()
    events = capture_events()

    sentry_sdk.get_isolation_scope().set_tag("request_data", False)

    @app.route("/")
    async def index():
        sentry_sdk.get_isolation_scope().set_tag("request_data", True)

        async def generate():
            for row in range(1000):
                assert sentry_sdk.get_isolation_scope()._tags["request_data"]

                yield str(row) + "\n"

        return Response(stream_with_context(generate)(), mimetype="text/csv")

    client = app.test_client()
    response = await client.get("/")
    assert (await response.get_data(as_text=True)) == "".join(
        str(row) + "\n" for row in range(1000)
    )
    assert not events
    assert not sentry_sdk.get_isolation_scope()._tags["request_data"]


@pytest.mark.asyncio
async def test_scoped_test_client(sentry_init):
    sentry_init(integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()

    @app.route("/")
    async def index():
        return "ok"

    async with app.test_client() as client:
        response = await client.get("/")
        assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", [ZeroDivisionError, Exception])
async def test_errorhandler_for_exception_swallows_exception(
    sentry_init, capture_events, exc_cls
):
    # In contrast to error handlers for a status code, error
    # handlers for exceptions can swallow the exception (this is
    # just how the Quart signal works)
    sentry_init(integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()
    events = capture_events()

    @app.route("/")
    async def index():
        1 / 0

    @app.errorhandler(exc_cls)
    async def zerodivision(e):
        return "ok"

    async with app.test_client() as client:
        response = await client.get("/")
        assert response.status_code == 200

    assert not events


@pytest.mark.asyncio
async def test_tracing_success(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()

    @app.before_request
    async def _():
        set_tag("before_request", "yes")

    @app.route("/message_tx")
    async def hi_tx():
        set_tag("view", "yes")
        capture_message("hi")
        return "ok"

    events = capture_events()

    async with app.test_client() as client:
        response = await client.get("/message_tx")
        assert response.status_code == 200

    message_event, transaction_event = events

    assert transaction_event["type"] == "transaction"
    assert transaction_event["transaction"] == "hi_tx"
    assert transaction_event["tags"]["view"] == "yes"
    assert transaction_event["tags"]["before_request"] == "yes"

    assert message_event["message"] == "hi"
    assert message_event["transaction"] == "hi_tx"
    assert message_event["tags"]["view"] == "yes"
    assert message_event["tags"]["before_request"] == "yes"


@pytest.mark.asyncio
async def test_tracing_error(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()

    events = capture_events()

    @app.route("/error")
    async def error():
        1 / 0

    async with app.test_client() as client:
        response = await client.get("/error")
        assert response.status_code == 500

    error_event, transaction_event = events

    assert transaction_event["type"] == "transaction"
    assert transaction_event["transaction"] == "error"

    assert error_event["transaction"] == "error"
    (exception,) = error_event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"


@pytest.mark.asyncio
async def test_class_based_views(sentry_init, capture_events):
    from quart.views import View

    sentry_init(integrations=[quart_sentry.QuartIntegration()])
    app = quart_app_factory()
    events = capture_events()

    @app.route("/")
    class HelloClass(View):
        methods = ["GET"]

        async def dispatch_request(self):
            capture_message("hi")
            return "ok"

    app.add_url_rule("/hello-class/", view_func=HelloClass.as_view("hello_class"))

    async with app.test_client() as client:
        response = await client.get("/hello-class/")
        assert response.status_code == 200

    (event,) = events

    assert event["message"] == "hi"
    assert event["transaction"] == "hello_class"


@pytest.mark.parametrize("endpoint", ["/sync/thread_ids", "/async/thread_ids"])
@pytest.mark.asyncio
async def test_active_thread_id(
    sentry_init, capture_envelopes, teardown_profiling, endpoint
):
    with mock.patch(
        "sentry_sdk.profiler.transaction_profiler.PROFILE_MINIMUM_SAMPLES", 0
    ):
        sentry_init(
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
        )
        app = quart_app_factory()

        envelopes = capture_envelopes()

        async with app.test_client() as client:
            response = await client.get(endpoint)
            assert response.status_code == 200

        data = json.loads(await response.get_data(as_text=True))

        envelopes = [envelope for envelope in envelopes]
        assert len(envelopes) == 1

        profiles = [item for item in envelopes[0].items if item.type == "profile"]
        assert len(profiles) == 1, envelopes[0].items

        for item in profiles:
            transactions = item.payload.json["transactions"]
            assert len(transactions) == 1
            assert str(data["active"]) == transactions[0]["active_thread_id"]

        transactions = [
            item for item in envelopes[0].items if item.type == "transaction"
        ]
        assert len(transactions) == 1

        for item in transactions:
            transaction = item.payload.json
            trace_context = transaction["contexts"]["trace"]
            assert str(data["active"]) == trace_context["data"]["thread.id"]


@pytest.mark.asyncio
async def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[quart_sentry.QuartIntegration()],
        traces_sample_rate=1.0,
    )
    app = quart_app_factory()
    events = capture_events()

    client = app.test_client()
    await client.get("/message")

    (_, event) = events

    assert event["contexts"]["trace"]["origin"] == "auto.http.quart"
