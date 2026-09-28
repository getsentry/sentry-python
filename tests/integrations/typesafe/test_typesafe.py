import json
from unittest import mock

import pytest
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
    SystemOneResponse,
    TypeSafeClient,
    Usage,
)

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.typesafe import TypeSafeIntegration


@pytest.fixture
def typesafe_response():
    return SystemOneResponse(
        model="jev-latest",
        usage=Usage(input_tokens=12, output_tokens=3),
        answers={
            "spam": NoulAnswer(noul=0.98),
            "tone": ChoiceAnswer(
                choice="friendly",
                confidence=0.9,
                probabilities={"friendly": 0.9, "hostile": 0.1},
            ),
            "urgency": ScoreAnswer(
                score=1.7,
                confidence=0.8,
                legend={0: "can wait", 1: "this week", 2: "today"},
                probabilities={0: 0.1, 1: 0.1, 2: 0.8},
            ),
            "spam_obj": NoulAnswer(noul=0.98),
            "tone_obj": ChoiceAnswer(
                choice="friendly",
                confidence=0.9,
                probabilities={"friendly": 0.9, "hostile": 0.1},
            ),
            "urgency_obj": ScoreAnswer(
                score=1.7,
                confidence=0.8,
                legend={0: "can wait", 1: "this week", 2: "today"},
                probabilities={0: 0.1, 1: 0.1, 2: 0.8},
            ),
        },
    )


def test_system_one(sentry_init, capture_items, typesafe_response):
    sentry_init(
        integrations=[TypeSafeIntegration()],
        traces_sample_rate=1.0,
        data_collection={},
    )

    client = TypeSafeClient(api_key="z")

    items = capture_items("span")

    with mock.patch.object(TypeSafeClient, "_request", return_value=typesafe_response):
        client.system_one(
            state={
                "subject": "Charged twice this month",
                "body": "I see two charges of $49. I only have one account. Please fix this ASAP.",
            },
            questions={
                "spam": {"type": "noul", "instructions": "Spam?"},
                "tone": {
                    "type": "choice",
                    "instructions": "Tone?",
                    "criteria": {"friendly": None, "hostile": None},
                },
                "quality": {
                    "type": "score",
                    "instructions": "Quality?",
                    "criteria": ["bad", "ok", "great"],
                },
                "spam_obj": Noul(instructions="Spam?"),
                "tone_obj": Choice(
                    instructions="Tone?",
                    criteria={"friendly": None, "hostile": None},
                ),
                "quality_obj": Score(
                    instructions="Quality?", criteria=["bad", "ok", "great"]
                ),
            },
        )

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    (span,) = (
        span
        for span in spans
        if span["attributes"].get("sentry.op") == OP.GEN_AI_EVALUATE
    )
    assert span["name"] == "evaluate jev-latest"

    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "typesafe"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "evaluate"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "jev-latest"

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "jev-latest"

    assert json.loads(span["attributes"][SPANDATA.GEN_AI_INPUT_MESSAGES]) == [
        {
            "type": "evaluation",
            "state": {
                "subject": "Charged twice this month",
                "body": "I see two charges of $49. I only have one account. Please fix this ASAP.",
            },
            "questions": {
                "spam": {
                    "type": "noul",
                    "instructions": "Spam?",
                },
                "tone": {
                    "type": "choice",
                    "instructions": "Tone?",
                    "criteria": {"friendly": None, "hostile": None},
                },
                "quality": {
                    "type": "score",
                    "instructions": "Quality?",
                    "criteria": ["bad", "ok", "great"],
                },
                "spam_obj": {
                    "type": "noul",
                    "instructions": "Spam?",
                },
                "tone_obj": {
                    "type": "choice",
                    "instructions": "Tone?",
                    "criteria": {"friendly": None, "hostile": None},
                },
                "quality_obj": {
                    "type": "score",
                    "instructions": "Quality?",
                    "criteria": ["bad", "ok", "great"],
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_system_one_async(sentry_init, capture_items, typesafe_response):
    sentry_init(
        integrations=[TypeSafeIntegration()],
        traces_sample_rate=1.0,
        data_collection={},
    )

    client = AsyncTypeSafeClient(api_key="z")

    items = capture_items("span")

    with mock.patch.object(
        AsyncTypeSafeClient,
        "_request",
        new_callable=mock.AsyncMock,
        return_value=typesafe_response,
    ):
        await client.system_one(
            state={
                "subject": "Charged twice this month",
                "body": "I see two charges of $49. I only have one account. Please fix this ASAP.",
            },
            questions={
                "spam": {"type": "noul", "instructions": "Spam?"},
                "tone": {
                    "type": "choice",
                    "instructions": "Tone?",
                    "criteria": {"friendly": None, "hostile": None},
                },
                "quality": {
                    "type": "score",
                    "instructions": "Quality?",
                    "criteria": ["bad", "ok", "great"],
                },
                "spam_obj": Noul(instructions="Spam?"),
                "tone_obj": Choice(
                    instructions="Tone?",
                    criteria={"friendly": None, "hostile": None},
                ),
                "quality_obj": Score(
                    instructions="Quality?", criteria=["bad", "ok", "great"]
                ),
            },
        )

    sentry_sdk.flush()

    spans = [item.payload for item in items]
    (span,) = (
        span
        for span in spans
        if span["attributes"].get("sentry.op") == OP.GEN_AI_EVALUATE
    )
    assert span["name"] == "evaluate jev-latest"

    assert span["attributes"][SPANDATA.GEN_AI_PROVIDER_NAME] == "typesafe"
    assert span["attributes"][SPANDATA.GEN_AI_OPERATION_NAME] == "evaluate"
    assert span["attributes"][SPANDATA.GEN_AI_REQUEST_MODEL] == "jev-latest"

    assert span["attributes"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "jev-latest"

    assert json.loads(span["attributes"][SPANDATA.GEN_AI_INPUT_MESSAGES]) == [
        {
            "type": "evaluation",
            "state": {
                "subject": "Charged twice this month",
                "body": "I see two charges of $49. I only have one account. Please fix this ASAP.",
            },
            "questions": {
                "spam": {
                    "type": "noul",
                    "instructions": "Spam?",
                },
                "tone": {
                    "type": "choice",
                    "instructions": "Tone?",
                    "criteria": {"friendly": None, "hostile": None},
                },
                "quality": {
                    "type": "score",
                    "instructions": "Quality?",
                    "criteria": ["bad", "ok", "great"],
                },
                "spam_obj": {
                    "type": "noul",
                    "instructions": "Spam?",
                },
                "tone_obj": {
                    "type": "choice",
                    "instructions": "Tone?",
                    "criteria": {"friendly": None, "hostile": None},
                },
                "quality_obj": {
                    "type": "score",
                    "instructions": "Quality?",
                    "criteria": ["bad", "ok", "great"],
                },
            },
        }
    ]
