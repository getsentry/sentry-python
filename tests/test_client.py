import pytest
from sentry_sdk import Client
from sentry_sdk.transport import Transport


def test_transport_option(monkeypatch):
    dsn = "https://foo@sentry.io/123"
    dsn2 = "https://bar@sentry.io/124"
    assert str(Client(dsn=dsn).dsn) == dsn
    assert Client().dsn is None
    with pytest.raises(ValueError):
        Client(dsn, transport=Transport(dsn2))
    with pytest.raises(ValueError):
        Client(dsn, transport=Transport(dsn))
    assert str(Client(transport=Transport(dsn2)).dsn) == dsn2

    monkeypatch.setenv("SENTRY_DSN", dsn)
    assert str(Client(transport=Transport(dsn2)).dsn) == dsn2
