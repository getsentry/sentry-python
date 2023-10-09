import json
import unittest.mock

import pytest

import trytond
from trytond.exceptions import TrytonException as TrytondBaseException
from trytond.exceptions import UserError as TrytondUserError
from trytond.exceptions import UserWarning as TrytondUserWarning
from trytond.exceptions import LoginException
from trytond.wsgi import app as trytond_app

from werkzeug.test import Client
from sentry_sdk import last_event_id
from sentry_sdk.integrations.trytond import TrytondWSGIIntegration


@pytest.fixture(scope="function")
def app(sentry_init):
    yield trytond_app


@pytest.fixture
def get_client(app):
    def inner():
        return Client(app)

    return inner


@pytest.mark.parametrize(
    "exception", [Exception("foo"), type("FooException", (Exception,), {})("bar")]
)
def test_exceptions_captured(
    sentry_init, app, capture_exceptions, get_client, exception
):
    sentry_init(integrations=[TrytondWSGIIntegration()])
    exceptions = capture_exceptions()

    unittest.mock.sentinel.exception = exception

    @app.route("/exception")
    def _(request):
        raise unittest.mock.sentinel.exception

    client = get_client()
    _ = client.get("/exception")

    (e,) = exceptions
    assert e is exception


@pytest.mark.parametrize(
    "exception",
    [
        TrytondUserError("title"),
        TrytondUserWarning("title", "details"),
        LoginException("title", "details"),
    ],
)
def test_trytonderrors_not_captured(
    sentry_init, app, capture_exceptions, get_client, exception
):
    sentry_init(integrations=[TrytondWSGIIntegration()])
    exceptions = capture_exceptions()

    unittest.mock.sentinel.exception = exception

    @app.route("/usererror")
    def _(request):
        raise unittest.mock.sentinel.exception

    client = get_client()
    _ = client.get("/usererror")

    assert not exceptions


@pytest.mark.skipif(
    trytond.__version__.split(".") < ["5", "4"], reason="At least Trytond-5.4 required"
)
def test_rpc_error_page(sentry_init, app, capture_events, get_client):
    """Test that, after initializing the Trytond-SentrySDK integration
    a custom error handler can be registered to the Trytond WSGI app so as to
    inform the event identifiers to the Tryton RPC client"""

    sentry_init(integrations=[TrytondWSGIIntegration()])
    events = capture_events()

    @app.route("/rpcerror", methods=["POST"])
    def _(request):
        raise Exception("foo")

    @app.error_handler
    def _(app, request, e):
        if isinstance(e, TrytondBaseException):
            return
        else:
            event_id = last_event_id()
            data = TrytondUserError(str(event_id), str(e))
            return app.make_response(request, data)

    client = get_client()

    # This would look like a natural Tryton RPC call
    _data = dict(
        id=42,  # request sequence
        method="class.method",  # rpc call
        params=[
            [1234],  # ids
            ["bar", "baz"],  # values
            dict(  # context
                client="12345678-9abc-def0-1234-56789abc",
                groups=[1],
                language="ca",
                language_direction="ltr",
            ),
        ],
    )
    response = client.post(
        "/rpcerror", content_type="application/json", data=json.dumps(_data)
    )

    (event,) = events
    (content, status, headers) = response
    data = json.loads(next(content))
    assert status == "200 OK"
    assert headers.get("Content-Type") == "application/json"
    assert data == dict(id=42, error=["UserError", [event["event_id"], "foo", None]])
