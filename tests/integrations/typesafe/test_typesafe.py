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


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_system_one(
    sentry_init,
    capture_items,
    typesafe_response,
    stream_gen_ai_spans,
    span_streaming,
):
    sentry_init(
        integrations=[TypeSafeIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    client = TypeSafeClient(api_key="z")

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with mock.patch.object(
            TypeSafeClient, "_request", return_value=typesafe_response
        ), sentry_sdk.start_transaction(name="typesafe"):
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

        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 12
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3

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

        assert json.loads(span["attributes"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "type": "evaluation",
                "answers": {
                    "spam": {
                        "type": "noul",
                        "noul": 0.98,
                    },
                    "tone": {
                        "type": "choice",
                        "choice": "friendly",
                        "probabilities": {"friendly": 0.9, "hostile": 0.1},
                        "confidence": 0.9,
                    },
                    "urgency": {
                        "type": "score",
                        "score": 1.7,
                        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
                        "confidence": 0.8,
                        "legend": {"0": "can wait", "1": "this week", "2": "today"},
                    },
                    "spam_obj": {
                        "type": "noul",
                        "noul": 0.98,
                    },
                    "tone_obj": {
                        "type": "choice",
                        "choice": "friendly",
                        "probabilities": {"friendly": 0.9, "hostile": 0.1},
                        "confidence": 0.9,
                    },
                    "urgency_obj": {
                        "type": "score",
                        "score": 1.7,
                        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
                        "confidence": 0.8,
                        "legend": {"0": "can wait", "1": "this week", "2": "today"},
                    },
                },
            }
        ]
    else:
        items = capture_items("transaction")

        with mock.patch.object(
            TypeSafeClient, "_request", return_value=typesafe_response
        ), sentry_sdk.start_transaction(name="typesafe"):
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

        (transaction,) = [item.payload for item in items]
        (span,) = transaction["spans"]
        assert span["description"] == "evaluate jev-latest"

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "typesafe"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "evaluate"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "jev-latest"

        assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "jev-latest"

        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 12
        assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3

        assert json.loads(span["data"][SPANDATA.GEN_AI_INPUT_MESSAGES]) == [
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

        assert json.loads(span["data"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "type": "evaluation",
                "answers": {
                    "spam": {
                        "type": "noul",
                        "noul": 0.98,
                    },
                    "tone": {
                        "type": "choice",
                        "choice": "friendly",
                        "probabilities": {"friendly": 0.9, "hostile": 0.1},
                        "confidence": 0.9,
                    },
                    "urgency": {
                        "type": "score",
                        "score": 1.7,
                        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
                        "confidence": 0.8,
                        "legend": {"0": "can wait", "1": "this week", "2": "today"},
                    },
                    "spam_obj": {
                        "type": "noul",
                        "noul": 0.98,
                    },
                    "tone_obj": {
                        "type": "choice",
                        "choice": "friendly",
                        "probabilities": {"friendly": 0.9, "hostile": 0.1},
                        "confidence": 0.9,
                    },
                    "urgency_obj": {
                        "type": "score",
                        "score": 1.7,
                        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
                        "confidence": 0.8,
                        "legend": {"0": "can wait", "1": "this week", "2": "today"},
                    },
                },
            }
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
async def test_system_one_async(
    sentry_init,
    capture_items,
    typesafe_response,
    stream_gen_ai_spans,
    span_streaming,
):
    sentry_init(
        integrations=[TypeSafeIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        stream_gen_ai_spans=stream_gen_ai_spans,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    client = AsyncTypeSafeClient(api_key="z")

    if span_streaming or stream_gen_ai_spans:
        items = capture_items("span")

        with mock.patch.object(
            AsyncTypeSafeClient,
            "_request",
            new_callable=mock.AsyncMock,
            return_value=typesafe_response,
        ), sentry_sdk.start_transaction(name="typesafe"):
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

        assert span["attributes"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 12
        assert span["attributes"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3

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

        assert json.loads(span["attributes"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "type": "evaluation",
                "answers": {
                    "spam": {
                        "type": "noul",
                        "noul": 0.98,
                    },
                    "tone": {
                        "type": "choice",
                        "choice": "friendly",
                        "probabilities": {"friendly": 0.9, "hostile": 0.1},
                        "confidence": 0.9,
                    },
                    "urgency": {
                        "type": "score",
                        "score": 1.7,
                        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
                        "confidence": 0.8,
                        "legend": {"0": "can wait", "1": "this week", "2": "today"},
                    },
                    "spam_obj": {
                        "type": "noul",
                        "noul": 0.98,
                    },
                    "tone_obj": {
                        "type": "choice",
                        "choice": "friendly",
                        "probabilities": {"friendly": 0.9, "hostile": 0.1},
                        "confidence": 0.9,
                    },
                    "urgency_obj": {
                        "type": "score",
                        "score": 1.7,
                        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
                        "confidence": 0.8,
                        "legend": {"0": "can wait", "1": "this week", "2": "today"},
                    },
                },
            }
        ]
    else:
        items = capture_items("transaction")

        with mock.patch.object(
            AsyncTypeSafeClient,
            "_request",
            new_callable=mock.AsyncMock,
            return_value=typesafe_response,
        ), sentry_sdk.start_transaction(name="typesafe"):
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

        (transaction,) = [item.payload for item in items]
        (span,) = transaction["spans"]
        assert span["description"] == "evaluate jev-latest"

        assert span["data"][SPANDATA.GEN_AI_PROVIDER_NAME] == "typesafe"
        assert span["data"][SPANDATA.GEN_AI_OPERATION_NAME] == "evaluate"
        assert span["data"][SPANDATA.GEN_AI_REQUEST_MODEL] == "jev-latest"

        assert span["data"][SPANDATA.GEN_AI_RESPONSE_MODEL] == "jev-latest"

        assert span["data"][SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] == 12
        assert span["data"][SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] == 3

        assert json.loads(span["data"][SPANDATA.GEN_AI_INPUT_MESSAGES]) == [
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

        assert json.loads(span["data"][SPANDATA.GEN_AI_OUTPUT_MESSAGES]) == [
            {
                "type": "evaluation",
                "answers": {
                    "spam": {
                        "type": "noul",
                        "noul": 0.98,
                    },
                    "tone": {
                        "type": "choice",
                        "choice": "friendly",
                        "probabilities": {"friendly": 0.9, "hostile": 0.1},
                        "confidence": 0.9,
                    },
                    "urgency": {
                        "type": "score",
                        "score": 1.7,
                        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
                        "confidence": 0.8,
                        "legend": {"0": "can wait", "1": "this week", "2": "today"},
                    },
                    "spam_obj": {
                        "type": "noul",
                        "noul": 0.98,
                    },
                    "tone_obj": {
                        "type": "choice",
                        "choice": "friendly",
                        "probabilities": {"friendly": 0.9, "hostile": 0.1},
                        "confidence": 0.9,
                    },
                    "urgency_obj": {
                        "type": "score",
                        "score": 1.7,
                        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
                        "confidence": 0.8,
                        "legend": {"0": "can wait", "1": "this week", "2": "today"},
                    },
                },
            }
        ]
