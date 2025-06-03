from sentry_sdk.integrations.openai_agents import OpenAIAgentsIntegration


def test_basic(sentry_init):
    sentry_init(integrations=[OpenAIAgentsIntegration()])

    import ipdb

    ipdb.set_trace()

    assert True
