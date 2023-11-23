import pytest

from sentry_sdk import Client


def test_send_to_spotlight(make_client):
    client = make_client(
        spotlight=False,
    )
    assert client.spotlight is None


@pytest.fixture
def make_client(request):
    def inner(**kwargs):
        return Client("http://foobar@test.com/132", **kwargs)

    return inner
