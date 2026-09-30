import json
from collections.abc import Mapping
from functools import wraps
from typing import TYPE_CHECKING, cast

import sentry_sdk
from sentry_sdk.ai.utils import get_start_span_function
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import (
    has_data_collection_enabled,
    has_span_streaming_enabled,
)

if TYPE_CHECKING:
    from typing import (
        Any,
        Callable,
        Literal,
        Mapping,
        NotRequired,
        Sequence,
        TypedDict,
        Union,
    )

    from typesafe_sdk import JSONContent
    from typesafe_sdk._core.question_types import Questions

    class NoulModel(TypedDict):
        type: Literal["noul"]
        instructions: NotRequired[JSONContent | None]

    class ChoiceModel(TypedDict):
        type: Literal["choice"]
        criteria: NotRequired[Mapping[str, JSONContent | None]]
        instructions: NotRequired[JSONContent | None]

    class ScoreModel(TypedDict):
        type: Literal["score"]
        criteria: NotRequired[Sequence[JSONContent]]
        instructions: NotRequired[JSONContent | None]

    class InputMessageModel(TypedDict):
        type: Literal["evaluation"]
        state: NotRequired[JSONContent]
        questions: NotRequired[dict[str, Union[NoulModel, ChoiceModel, ScoreModel]]]

    class NoulEvaluationModel(TypedDict):
        type: Literal["noul"]
        noul: float

    class ChoiceEvaluationModel(TypedDict):
        type: Literal["choice"]
        choice: str
        probabilities: dict[str, float]
        confidence: float

    class ScoreEvaluationModel(TypedDict):
        type: Literal["score"]
        score: float
        probabilities: dict[int, float]
        confidence: float
        legend: dict[int, Union[str, dict[str, Any], list[Any]]]


try:
    from typesafe_sdk import Choice, ChoiceAnswer, Noul, NoulAnswer, Score, ScoreAnswer
    from typesafe_sdk._core.client.aio.client import AsyncTypeSafeClient
    from typesafe_sdk._core.client.sync.client import TypeSafeClient
    from typesafe_sdk._core.response_types import Answer, SystemOneResponse
except ImportError:
    raise DidNotEnable("typesafe-sdk not installed")


class TypeSafeIntegration(Integration):
    identifier = "typesafe"
    origin = f"auto.ai.{identifier}"

    @staticmethod
    def setup_once() -> None:
        TypeSafeClient.system_one = _wrap_system_one(TypeSafeClient.system_one)  # type: ignore[method-assign]
        AsyncTypeSafeClient.system_one = _wrap_system_one_async(  # type: ignore[method-assign]
            AsyncTypeSafeClient.system_one
        )


def _transform_questions(
    questions: "Questions",
) -> "dict[str, Union[NoulModel, ChoiceModel, ScoreModel]]":
    transformed_questions: "dict[str, Union[NoulModel, ChoiceModel, ScoreModel]]" = {}
    for name, question in questions.items():
        if isinstance(question, Noul):
            noul: "NoulModel" = {
                "type": "noul",
            }

            if question.instructions is not None:
                noul["instructions"] = question.instructions

            transformed_questions[name] = noul
            continue

        if isinstance(question, Choice):
            choice: "ChoiceModel" = {
                "type": "choice",
                "criteria": question.criteria,
            }

            if question.instructions is not None:
                choice["instructions"] = question.instructions

            transformed_questions[name] = choice
            continue

        if isinstance(question, Score):
            score: "ScoreModel" = {
                "type": "score",
                "criteria": question.criteria,
            }

            if question.instructions is not None:
                score["instructions"] = question.instructions

            transformed_questions[name] = score
            continue

        if not isinstance(question, dict):
            continue

        question_type = question.get("type")
        if question_type == "noul":
            noul = {
                "type": question_type,
            }
            if "instructions" in question:
                noul["instructions"] = question["instructions"]

            transformed_questions[name] = noul
            continue

        if question_type == "choice":
            choice = {
                "type": question_type,
            }
            if "criteria" in question:
                choice["criteria"] = cast(
                    "Mapping[str, JSONContent | None]", question["criteria"]
                )
            if "instructions" in question:
                choice["instructions"] = question["instructions"]

            transformed_questions[name] = choice
            continue

        if question_type == "score":
            score = {
                "type": question_type,
            }
            if "criteria" in question:
                score["criteria"] = cast("Sequence[JSONContent]", question["criteria"])
            if "instructions" in question:
                score["instructions"] = question["instructions"]

            transformed_questions[name] = score
            continue

    return transformed_questions


def _transform_evaluation_answers(
    answers: "dict[str, Answer]",
) -> (
    "dict[str, Union[NoulEvaluationModel, ChoiceEvaluationModel, ScoreEvaluationModel]]"
):
    items: "dict[str, Union[NoulEvaluationModel, ChoiceEvaluationModel, ScoreEvaluationModel]]" = {}
    for name, answer in answers.items():
        if isinstance(answer, NoulAnswer):
            items[name] = {
                "type": "noul",
                "noul": answer.noul,
            }
            continue

        if isinstance(answer, ChoiceAnswer):
            items[name] = {
                "type": "choice",
                "choice": answer.choice,
                "probabilities": answer.probabilities,
                "confidence": answer.confidence,
            }
            continue

        if isinstance(answer, ScoreAnswer):
            items[name] = {
                "type": "score",
                "score": answer.score,
                "probabilities": answer.probabilities,
                "confidence": answer.confidence,
                "legend": answer.legend,
            }
            continue

    return items


def _wrap_system_one(f: "Callable[..., Any]") -> "Callable[..., Any]":
    @wraps(f)
    def wrap_system_one(self: "TypeSafeClient", *args: "Any", **kwargs: "Any") -> "Any":
        client = sentry_sdk.get_client()
        integration = client.get_integration(TypeSafeIntegration)
        if integration is None:
            return f(self, *args, **kwargs)

        model = kwargs.get("model")
        if (
            model is None
            and hasattr(self, "_config")
            and hasattr(self._config, "default_model")
        ):
            model = self._config.default_model

        if has_span_streaming_enabled(client.options):
            span = sentry_sdk.traces.start_span(
                name=f"evaluate {model}".strip(),
                attributes={
                    "sentry.op": OP.GEN_AI_EVALUATE,
                    "sentry.origin": TypeSafeIntegration.origin,
                    SPANDATA.GEN_AI_PROVIDER_NAME: "typesafe",
                    SPANDATA.GEN_AI_OPERATION_NAME: "evaluate",
                },
            )
            set_on_span = span.set_attribute
        else:
            span = get_start_span_function()(
                op=OP.GEN_AI_EVALUATE,
                name=f"evaluate {model}".strip(),
                origin=TypeSafeIntegration.origin,
            )
            span.set_data(SPANDATA.GEN_AI_PROVIDER_NAME, "typesafe")
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "evaluate")
            set_on_span = span.set_data

        with span:
            if model is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_MODEL, model)

            if (
                has_data_collection_enabled(client.options)
                and client.options["data_collection"]["gen_ai"]["inputs"]
            ) or (
                not has_data_collection_enabled(client.options)
                and should_send_default_pii()
            ):
                input_message: "InputMessageModel" = {
                    "type": "evaluation",
                }

                state = args[0] if len(args) > 0 else kwargs.get("state")
                if state is not None:
                    input_message["state"] = state

                questions = args[1] if len(args) > 1 else kwargs.get("questions")
                if isinstance(questions, Mapping):
                    input_message["questions"] = _transform_questions(questions)

                set_on_span(SPANDATA.GEN_AI_INPUT_MESSAGES, json.dumps([input_message]))

            response = f(self, *args, **kwargs)

            if not isinstance(response, SystemOneResponse):
                return response

            set_on_span(SPANDATA.GEN_AI_RESPONSE_MODEL, response.model)

            if response.usage.input_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, response.usage.input_tokens
                )

            if response.usage.output_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, response.usage.output_tokens
                )

            if (
                has_data_collection_enabled(client.options)
                and client.options["data_collection"]["gen_ai"]["outputs"]
            ) or (
                not has_data_collection_enabled(client.options)
                and should_send_default_pii()
            ):
                set_on_span(
                    SPANDATA.GEN_AI_OUTPUT_MESSAGES,
                    json.dumps(
                        [
                            {
                                "type": "evaluation",
                                "answers": _transform_evaluation_answers(
                                    response.answers
                                ),
                            }
                        ]
                    ),
                )

            return response

    return wrap_system_one


def _wrap_system_one_async(f: "Callable[..., Any]") -> "Callable[..., Any]":
    @wraps(f)
    async def wrap_system_one_async(
        self: "AsyncTypeSafeClient", *args: "Any", **kwargs: "Any"
    ) -> "Any":
        client = sentry_sdk.get_client()
        integration = client.get_integration(TypeSafeIntegration)
        if integration is None:
            return await f(self, *args, **kwargs)

        model = kwargs.get("model")
        if (
            model is None
            and hasattr(self, "_config")
            and hasattr(self._config, "default_model")
        ):
            model = self._config.default_model

        if has_span_streaming_enabled(client.options):
            span = sentry_sdk.traces.start_span(
                name=f"evaluate {model}".strip(),
                attributes={
                    "sentry.op": OP.GEN_AI_EVALUATE,
                    "sentry.origin": TypeSafeIntegration.origin,
                    SPANDATA.GEN_AI_PROVIDER_NAME: "typesafe",
                    SPANDATA.GEN_AI_OPERATION_NAME: "evaluate",
                },
            )
            set_on_span = span.set_attribute
        else:
            span = get_start_span_function()(
                op=OP.GEN_AI_EVALUATE,
                name=f"evaluate {model}".strip(),
                origin=TypeSafeIntegration.origin,
            )
            span.set_data(SPANDATA.GEN_AI_PROVIDER_NAME, "typesafe")
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "evaluate")
            set_on_span = span.set_data

        with span:
            if model is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_MODEL, model)

            if (
                has_data_collection_enabled(client.options)
                and client.options["data_collection"]["gen_ai"]["inputs"]
            ) or (
                not has_data_collection_enabled(client.options)
                and should_send_default_pii()
            ):
                input_message: "InputMessageModel" = {
                    "type": "evaluation",
                }

                state = args[0] if len(args) > 0 else kwargs.get("state")
                if state is not None:
                    input_message["state"] = state

                questions = args[1] if len(args) > 1 else kwargs.get("questions")
                if isinstance(questions, Mapping):
                    input_message["questions"] = _transform_questions(questions)

                set_on_span(SPANDATA.GEN_AI_INPUT_MESSAGES, json.dumps([input_message]))

            response = await f(self, *args, **kwargs)

            if not isinstance(response, SystemOneResponse):
                return response

            set_on_span(SPANDATA.GEN_AI_RESPONSE_MODEL, response.model)

            if response.usage.input_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, response.usage.input_tokens
                )

            if response.usage.output_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, response.usage.output_tokens
                )

            if (
                has_data_collection_enabled(client.options)
                and client.options["data_collection"]["gen_ai"]["outputs"]
            ) or (
                not has_data_collection_enabled(client.options)
                and should_send_default_pii()
            ):
                set_on_span(
                    SPANDATA.GEN_AI_OUTPUT_MESSAGES,
                    json.dumps(
                        [
                            {
                                "type": "evaluation",
                                "answers": _transform_evaluation_answers(
                                    response.answers
                                ),
                            }
                        ]
                    ),
                )

            return response

    return wrap_system_one_async
