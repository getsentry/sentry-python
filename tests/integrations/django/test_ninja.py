"""Tests for django-ninja integration with transaction_style="function_name"."""

import pytest

django = pytest.importorskip("django")
ninja = pytest.importorskip("ninja")

from sentry_sdk.integrations.django import DjangoIntegration


@pytest.mark.parametrize(
    "transaction_style,url,expected_transaction,expected_source",
    [
        (
            "function_name",
            "/ninja/ninja-message",
            "tests.integrations.django.myapp.views.ninja_message",
            "component",
        ),
        (
            "url",
            "/ninja/ninja-message",
            "/ninja/ninja-message",
            "route",
        ),
    ],
)
def test_ninja_transaction_style(
    sentry_init,
    client,
    capture_events,
    transaction_style,
    url,
    expected_transaction,
    expected_source,
):
    """Test that django-ninja endpoints work correctly with different transaction styles."""
    sentry_init(
        integrations=[DjangoIntegration(transaction_style=transaction_style)],
        send_default_pii=True,
    )
    events = capture_events()

    # Make the request
    response = client.get(url)
    assert response.status_code == 200

    # Check the captured event
    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}


def test_ninja_multiple_methods_same_path(
    sentry_init,
    client,
    capture_events,
):
    """Test that ninja endpoints with multiple HTTP methods on same path work correctly."""
    sentry_init(
        integrations=[DjangoIntegration(transaction_style="function_name")],
        send_default_pii=True,
    )
    events = capture_events()

    # Test GET
    response = client.get("/ninja/ninja-hello")
    assert response.status_code == 200

    # Test POST - note that ninja-hello has both GET and POST handlers
    response = client.post("/ninja/ninja-hello")
    assert response.status_code == 200

    # We should have 0 events because ninja-hello doesn't capture messages
    # This test just validates that the endpoints don't error and can be properly
    # resolved by the integration without crashing
    assert len(events) == 0

