
import json
import pytest

pytest.importorskip("trytond")

from trytond.exceptions import UserError as TrytondUserError
from trytond.exceptions import UserWarning as TrytondUserWarning
from trytond.exceptions import LoginException
from trytond.wsgi import app as trytond_app

from werkzeug.test import Client
from sentry_sdk import capture_message
from sentry_sdk.integrations.trytond import TrytondWSGIIntegration


@pytest.fixture(scope="function")
def app(sentry_init):
    yield trytond_app


@pytest.fixture
def get_client(app):
    def inner():
        return Client(app)
    return inner


# def test_errors(sentry_init, app, capture_events, get_client):
#     sentry_init(integrations=[TrytondWSGIIntegration(app)])
#     # events = capture_events()
#     client = get_client()
#     response = client.post(
#         "/", content_type="application/json",
#         data=json.dumps(dict(method='common.server.version', params=dict())))
#     assert response[1] == "200 OK"
# #
# #     event, = events
# #     assert event["message"] == "hi"
# #     assert "data" not in event["request"]
# #     assert event["request"]["url"] == "http://localhost/message"
#     assert True


@pytest.mark.parametrize("exception", [
    Exception('foo'),
    type('FooException', (Exception,), {})('bar'),
])
def test_exceptions_captured(sentry_init, app, capture_exceptions, get_client, exception):
    sentry_init(integrations=[TrytondWSGIIntegration()])
    exceptions = capture_exceptions()

    @app.route('/exception')
    def _(request):
        raise exception

    client = get_client()
    response = client.get("/exception")

    (e,) = exceptions
    assert e is exception


@pytest.mark.parametrize("exception", [
    TrytondUserError('title'),
    TrytondUserWarning('title', 'details'),
    LoginException('title', 'details'),
])
def test_trytonderrors_not_captured(sentry_init, app, capture_exceptions, get_client, exception):
    sentry_init(integrations=[TrytondWSGIIntegration()])
    exceptions = capture_exceptions()

    @app.route('/usererror')
    def _(request):
        raise exception

    client = get_client()
    response = client.get("/usererror")

    assert not exceptions
