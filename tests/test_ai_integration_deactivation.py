import json
import subprocess
import sys
from textwrap import dedent

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


try:
    from sentry_sdk.integrations.openai_agents import OpenAIAgentsIntegration

    has_openai_agents = True
except Exception:
    has_openai_agents = False

try:
    from sentry_sdk.integrations.pydantic_ai import PydanticAIIntegration

    has_pydantic_ai = True
except Exception:
    has_pydantic_ai = False


pytestmark = pytest.mark.skipif(
    not (
        has_langchain
        and has_openai
        and has_anthropic
        and has_openai_agents
        and has_pydantic_ai
    ),
    reason="Requires langchain, openai, and anthropic packages to be installed",
)


def test_integration_deactivates_map_exists():
    assert "langchain" in _INTEGRATION_DEACTIVATES
    assert "openai" in _INTEGRATION_DEACTIVATES["langchain"]
    assert "anthropic" in _INTEGRATION_DEACTIVATES["langchain"]
    assert "google_genai" in _INTEGRATION_DEACTIVATES["langchain"]
    assert "openai_agents" in _INTEGRATION_DEACTIVATES
    assert "openai" in _INTEGRATION_DEACTIVATES["openai_agents"]
    assert "pydantic_ai" in _INTEGRATION_DEACTIVATES
    assert "openai" in _INTEGRATION_DEACTIVATES["pydantic_ai"]
    assert "anthropic" in _INTEGRATION_DEACTIVATES["pydantic_ai"]


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


def test_disabling_integrations_allows_openai_and_anthropic(
    sentry_init, reset_integrations
):
    sentry_init(
        default_integrations=False,
        auto_enabling_integrations=True,
        disabled_integrations=[
            LangchainIntegration,
            OpenAIAgentsIntegration,
            PydanticAIIntegration,
        ],
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


OPENAI_CALL = (
    "openai.OpenAI(api_key='x', base_url='http://localhost:1', max_retries=0)"
    ".chat.completions.create(model='gpt-4o', messages=[{'role': 'user', 'content': 'hi'}])"
)
ANTHROPIC_CALL = (
    "anthropic.Anthropic(api_key='x', base_url='http://localhost:1', max_retries=0)"
    ".messages.create(model='claude', max_tokens=1, messages=[{'role': 'user', 'content': 'hi'}])"
)
GOOGLE_GENAI_CALL = (
    "google.genai.Client(api_key='x', http_options={'base_url': 'http://localhost:1', 'retry_options': {'attempts': 1}})"
    ".models.generate_content(model='gemini', contents='hi')"
)


@pytest.mark.parametrize(
    "deactivator_trigger, target, target_module, target_call",
    [
        pytest.param(
            "langchain_core", "openai", "openai", OPENAI_CALL, id="langchain-openai"
        ),
        pytest.param(
            "langchain_core",
            "anthropic",
            "anthropic",
            ANTHROPIC_CALL,
            id="langchain-anthropic",
        ),
        pytest.param(
            "langchain_core",
            "google_genai",
            "google.genai",
            GOOGLE_GENAI_CALL,
            id="langchain-google_genai",
        ),
        pytest.param(
            "agents", "openai", "openai", OPENAI_CALL, id="openai_agents-openai"
        ),
        pytest.param(
            "pydantic_ai", "openai", "openai", OPENAI_CALL, id="pydantic_ai-openai"
        ),
        pytest.param(
            "pydantic_ai",
            "anthropic",
            "anthropic",
            ANTHROPIC_CALL,
            id="pydantic_ai-anthropic",
        ),
    ],
)
@pytest.mark.parametrize(
    "explicit_target",
    [
        pytest.param(False, id="auto_enabled_target"),
        pytest.param(True, id="explicit_target"),
    ],
)
def test_deactivator_imported_after_target(
    tmp_path, deactivator_trigger, target, target_module, target_call, explicit_target
):
    target_integration = {
        "openai": "OpenAIIntegration",
        "anthropic": "AnthropicIntegration",
        "google_genai": "GoogleGenAIIntegration",
    }[target]

    app = tmp_path / "app.py"
    app.write_text(
        dedent(
            """
            import json
            import sys

            import sentry_sdk

            # Makes the other deactivating libraries look uninstalled
            for name in {other_deactivator_triggers!r}:
                sys.modules[name] = None

            integrations = []
            if {explicit_target}:
                from sentry_sdk.integrations.{target} import {target_integration}

                integrations.append({target_integration}())

            transactions = []

            def before_send_transaction(event, hint):
                transactions.append(event)
                return None

            sentry_sdk.init(
                traces_sample_rate=1.0,
                before_send_transaction=before_send_transaction,
                integrations=integrations,
                _experiments={{"deferred_auto_enabling_integrations": True}},
            )

            import {target_module}
            import {deactivator_trigger}

            with sentry_sdk.start_transaction(name="ai"):
                try:
                    {target_call}
                except Exception:
                    pass

            print(json.dumps([
                span["origin"]
                for span in transactions[0]["spans"]
                if span["origin"].startswith("auto.ai.")
            ]))
            """
        ).format(
            target=target,
            other_deactivator_triggers=sorted(
                {"agents", "langchain_core", "pydantic_ai"} - {deactivator_trigger}
            ),
            explicit_target=explicit_target,
            target_integration=target_integration,
            target_module=target_module,
            deactivator_trigger=deactivator_trigger,
            target_call=target_call,
        )
    )

    ai_span_origins = json.loads(subprocess.check_output([sys.executable, str(app)]))

    assert ai_span_origins == ([f"auto.ai.{target}"] if explicit_target else [])
