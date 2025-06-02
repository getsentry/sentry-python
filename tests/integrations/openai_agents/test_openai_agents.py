from sentry_sdk.integrations.openai_agents import OpenAIAgentsIntegration


def test_basic(sentry_init):
    sentry_init(integrations=[OpenAIAgentsIntegration()])

    assert True
