import sys
import pytest

django = pytest.importorskip("django")


from django.test import Client
from django.test.utils import setup_test_environment

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from sentry_sdk import Hub


@pytest.fixture
def setup():
    setup_test_environment()


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def capture_exceptions(monkeypatch):
    errors = []

    def capture_exception(error=None):
        errors.append(error or sys.exc_info()[1])

    monkeypatch.setattr(Hub.current, "capture_exception", capture_exception)
    return errors


def test_scope_working(client):
    response = client.get(reverse("self_check"))
    assert response.status_code == 200


def test_view_exceptions(client, capture_exceptions):
    with pytest.raises(ZeroDivisionError) as exc:
        client.get(reverse("view_exc"))
    assert capture_exceptions == [exc.value]


def test_middleware_exceptions(client, capture_exceptions):
    with pytest.raises(ZeroDivisionError) as exc:
        client.get(reverse("middleware_exc"))
    assert capture_exceptions == [exc.value]


def test_get_dsn(client):
    response = client.get(reverse("get_dsn"))
    assert response.content == b"LOL!"


def test_request_captured(client, capture_events):
    response = client.get(reverse('message'))
    assert response.content == b'ok'

    event, = capture_events
    assert event['request'] == {
        'cookies': {},
        'env': {
            'REMOTE_ADDR': '127.0.0.1',
            'SERVER_NAME': 'testserver',
            'SERVER_PORT': '80'
        },
        'headers': {'Cookie': ''},
        'method': 'GET',
        'query_string': '',
        'url': 'http://testserver/message'
    }
