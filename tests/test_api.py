import mock

from sentry_sdk import (
    get_current_span_or_transaction,
    get_current_span,
    get_current_transaction,
)


def test_get_current_span():
    fake_hub = mock.MagicMock()
    fake_hub.scope = mock.MagicMock()

    fake_hub.scope.span = mock.MagicMock()
    assert get_current_span(fake_hub) == fake_hub.scope.span

    fake_hub.scope.span = None
    assert get_current_span(fake_hub) is None


def test_get_current_transaction():
    fake_hub = mock.MagicMock()
    fake_hub.scope = mock.MagicMock()

    fake_hub.scope.transaction = mock.MagicMock()
    assert get_current_transaction(fake_hub) == fake_hub.scope.transaction

    fake_hub.scope.transaction = None
    assert get_current_transaction(fake_hub) is None


def test_get_current_span_or_transaction():
    fake_hub = mock.MagicMock()
    fake_hub.scope = mock.MagicMock()

    fake_hub.scope.span = mock.MagicMock()
    fake_hub.scope.transaction = None
    assert get_current_span_or_transaction(fake_hub) == fake_hub.scope.span

    fake_hub.scope.span = None
    fake_hub.scope.transaction = mock.MagicMock()
    assert get_current_span_or_transaction(fake_hub) == fake_hub.scope.transaction

    fake_hub.scope.span = None
    fake_hub.scope.transaction = None
    assert get_current_span_or_transaction(fake_hub) is None
