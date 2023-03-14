import mock

from sentry_sdk import (
    configure_scope,
    get_current_span_or_transaction,
    get_current_span,
    get_current_transaction,
    start_transaction,
)


def test_get_current_span():
    fake_hub = mock.MagicMock()
    fake_hub.scope = mock.MagicMock()

    fake_hub.scope.span = mock.MagicMock()
    assert get_current_span(fake_hub) == fake_hub.scope.span

    fake_hub.scope.span = None
    assert get_current_span(fake_hub) is None


def test_get_current_span_default_hub(sentry_init):
    sentry_init()

    assert get_current_span() is None

    with configure_scope() as scope:
        fake_span = mock.MagicMock()
        scope.span = fake_span

        assert get_current_span() == fake_span


def test_get_current_transaction():
    fake_hub = mock.MagicMock()
    fake_hub.scope = mock.MagicMock()

    fake_hub.scope.transaction = mock.MagicMock()
    assert get_current_transaction(fake_hub) == fake_hub.scope.transaction

    fake_hub.scope.transaction = None
    assert get_current_transaction(fake_hub) is None


def test_get_current_transaction_default_hub(sentry_init):
    sentry_init()

    assert get_current_transaction() is None

    with start_transaction() as new_transaction:
        assert get_current_transaction() == new_transaction


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


def test_get_current_span_or_transaction_default_hub(sentry_init):
    sentry_init()

    assert get_current_span_or_transaction() is None

    with start_transaction() as new_transaction:
        assert get_current_span_or_transaction() == new_transaction

        with configure_scope() as scope:
            fake_span = mock.MagicMock()
            scope.span = fake_span

            assert get_current_span_or_transaction() == fake_span
