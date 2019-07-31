
import json
import pytest


pytest.importorskip("trytond")

from trytond.exceptions import UserError as TrytondUserError
from trytond.wsgi import app as trytond_app
from trytond.wsgi import TrytondWSGI
from sentry_sdk import capture_message

from werkzeug.test import Client

from sentry_sdk.integrations.trytond import TrytondWSGIIntegration



@pytest.fixture(scope="function")
def app(sentry_init):

    # trytond_app = TrytondWSGI()

    @trytond_app.route("/message")
    def hi(*args, **kwargs):
        return "hi"

    yield trytond_app


@pytest.fixture
def get_client(app):
    def inner():
        return Client(app)
    return inner

#
# def test_errors(sentry_init, app, capture_events, get_client):
#     sentry_init(integrations=[TrytondWSGIIntegration(app)])
#     # events = capture_events()
#     client = get_client()
#     response = client.post(
#         "/", content_type="application/json",
#         data=json.dumps(dict(method='common.db.list', params=dict())))
#     assert response[1] == "200 OK"
# #
# #     event, = events
# #     assert event["message"] == "hi"
# #     assert "data" not in event["request"]
# #     assert event["request"]["url"] == "http://localhost/message"
#     assert True

def test_errors_captured(sentry_init, app, capture_exceptions, get_client):
    sentry_init(integrations=[TrytondWSGIIntegration()])
    exceptions = capture_exceptions()

    e = Exception('foo')
    @app.route('/usererror')
    def _(request):
        raise e

    client = get_client()
    response = client.get("/usererror")

    (exception,) = exceptions
    assert exception is e


def test_usererrors_not_captured(sentry_init, app, capture_exceptions, get_client):
    sentry_init(integrations=[TrytondWSGIIntegration()])
    exceptions = capture_exceptions()

    e = TrytondUserError('foo')
    @app.route('/usererror')
    def _(request):
        raise e

    client = get_client()
    response = client.get("/usererror")

    assert not exceptions
