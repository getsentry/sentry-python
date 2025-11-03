import pytest

from sentry_sdk import get_client
from sentry_sdk.integrations import _INTEGRATION_DEACTIVATES


try:
    from sentry_sdk.integrations.langchain import LangchainIntegration

    has_langchain = True
except Exception:
    has_langchain = False

try:
    from sentry_sdk.integrations.openai import OpenAIIntegration

    has_openai = True
except Exception:
    has_openai = False

try:
    from sentry_sdk.integrations.anthropic import AnthropicIntegration

    has_anthropic = True
except Exception:
    has_anthropic = False


pytestmark = pytest.mark.skipif(
    not (has_langchain and has_openai and has_anthropic),
    reason="Requires langchain, openai, and anthropic packages to be installed",
)


def test_integration_deactivates_map_exists():
    assert "langchain" in _INTEGRATION_DEACTIVATES
    assert "openai" in _INTEGRATION_DEACTIVATES["langchain"]
    assert "anthropic" in _INTEGRATION_DEACTIVATES["langchain"]


def test_langchain_auto_deactivates_openai_and_anthropic(
    sentry_init, reset_integrations
):
    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=True,
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    if LangchainIntegration in integration_types:
        assert OpenAIIntegration not in integration_types
        assert AnthropicIntegration not in integration_types


def test_user_can_override_with_explicit_openai(sentry_init, reset_integrations):
    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=True,
        integrations=[OpenAIIntegration()],
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    assert OpenAIIntegration in integration_types


def test_user_can_override_with_explicit_anthropic(sentry_init, reset_integrations):
    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=True,
        integrations=[AnthropicIntegration()],
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    assert AnthropicIntegration in integration_types


def test_user_can_override_with_both_explicit_integrations(
    sentry_init, reset_integrations
):
    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=True,
        integrations=[OpenAIIntegration(), AnthropicIntegration()],
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    assert OpenAIIntegration in integration_types
    assert AnthropicIntegration in integration_types


def test_disabling_langchain_allows_openai_and_anthropic(
    sentry_init, reset_integrations
):
    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=True,
        disabled_integrations=[LangchainIntegration],
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    assert LangchainIntegration not in integration_types


def test_explicit_langchain_still_deactivates_others(sentry_init, reset_integrations):
    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[LangchainIntegration()],
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    if LangchainIntegration in integration_types:
        assert OpenAIIntegration not in integration_types
        assert AnthropicIntegration not in integration_types


def test_langchain_and_openai_both_explicit_both_active(
    sentry_init, reset_integrations
):
    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[LangchainIntegration(), OpenAIIntegration()],
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    assert LangchainIntegration in integration_types
    assert OpenAIIntegration in integration_types


def test_no_langchain_means_openai_and_anthropic_can_auto_enable(
    sentry_init, reset_integrations, monkeypatch
):
    import sys
    import sentry_sdk.integrations

    old_iter = sentry_sdk.integrations.iter_default_integrations

    def filtered_iter(with_auto_enabling):
        for cls in old_iter(with_auto_enabling):
            if cls.identifier != "langchain":
                yield cls

    monkeypatch.setattr(
        sentry_sdk.integrations, "iter_default_integrations", filtered_iter
    )

    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=True,
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    assert LangchainIntegration not in integration_types


def test_deactivation_with_default_integrations_enabled(
    sentry_init, reset_integrations
):
    sentry_init(
        default_integrations=True,
        auto_enabling_integrations=True,
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    if LangchainIntegration in integration_types:
        assert OpenAIIntegration not in integration_types
        assert AnthropicIntegration not in integration_types


def test_only_auto_enabling_integrations_without_defaults(
    sentry_init, reset_integrations
):
    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=True,
    )

    client = get_client()
    integration_types = {
        type(integration) for integration in client.integrations.values()
    }

    if LangchainIntegration in integration_types:
        assert OpenAIIntegration not in integration_types
        assert AnthropicIntegration not in integration_types
