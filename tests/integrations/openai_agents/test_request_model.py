from agents import Agent
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI

from sentry_sdk.integrations.openai_agents.utils import _get_request_model_name


def test_request_model_name_prefers_model_instance_over_sentry_fallback():
    client = AsyncOpenAI(api_key="test-key")
    model = OpenAIResponsesModel(model="gpt-4", openai_client=client)
    agent = Agent(name="test_agent", model=model)
    agent._sentry_request_model = "fallback-model"

    assert _get_request_model_name(agent) == "gpt-4"


def test_request_model_name_prefers_string_model_over_sentry_fallback():
    agent = Agent(name="test_agent", model="gpt-4o-mini")
    agent._sentry_request_model = "fallback-model"

    assert _get_request_model_name(agent) == "gpt-4o-mini"


def test_request_model_name_falls_back_to_sentry_request_model():
    agent = Agent(name="test_agent", model=None)
    agent._sentry_request_model = "default-from-run-config"

    assert _get_request_model_name(agent) == "default-from-run-config"


def test_request_model_name_returns_none_without_model_or_fallback():
    agent = Agent(name="test_agent", model=None)

    assert _get_request_model_name(agent) is None
