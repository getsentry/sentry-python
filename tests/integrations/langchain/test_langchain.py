import pytest

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.langchain import LangchainIntegration


@pytest.mark.parametrize(
    "send_default_pii, include_prompts",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_nonstreaming_chat_completion(
    sentry_init, capture_events, send_default_pii, include_prompts
):
    sentry_init(
        integrations=[LangchainIntegration(include_prompts=include_prompts)],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    with start_transaction():
        pass

    tx = events[0]
    assert tx["type"] == "transaction"
    span = tx["spans"][0]
    assert span["op"] == "ai.chat_completions.create.openai"

    if send_default_pii and include_prompts:
        assert "hello" in span["data"]["ai.input_messages"][0]["content"]
        assert "the model response" in span["data"]["ai.responses"][0]["content"]
    else:
        assert "ai.input_messages" not in span["data"]
        assert "ai.responses" not in span["data"]

    assert span["data"][SPANDATA.AI_COMPLETION_TOKENS_USED] == 10
    assert span["data"][SPANDATA.AI_PROMPT_TOKENS_USED] == 20
    assert span["data"][SPANDATA.AI_TOTAL_TOKENS_USED] == 30
