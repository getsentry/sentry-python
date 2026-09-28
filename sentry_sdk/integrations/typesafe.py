import json
from collections.abc import Mapping
from functools import wraps
from typing import TYPE_CHECKING, cast

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration

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


try:
    from typesafe_sdk import Choice, Noul, Score
    from typesafe_sdk._core.client.aio.client import AsyncTypeSafeClient
    from typesafe_sdk._core.client.sync.client import TypeSafeClient
    from typesafe_sdk._core.response_types import SystemOneResponse
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

        with sentry_sdk.start_span(
            name=f"evaluate {model}".strip(),
            attributes={
                "sentry.op": OP.GEN_AI_EVALUATE,
                "sentry.origin": TypeSafeIntegration.origin,
                SPANDATA.GEN_AI_PROVIDER_NAME: "typesafe",
                SPANDATA.GEN_AI_OPERATION_NAME: "evaluate",
            },
        ) as span:
            if model is not None:
                span.set_attribute(SPANDATA.GEN_AI_REQUEST_MODEL, model)

            if client.options["data_collection"]["gen_ai"]["inputs"]:
                input_message: "InputMessageModel" = {
                    "type": "evaluation",
                }

                state = args[0] if len(args) > 0 else kwargs.get("state")
                if state is not None:
                    input_message["state"] = state

                questions = args[1] if len(args) > 1 else kwargs.get("questions")
                if isinstance(questions, Mapping):
                    input_message["questions"] = _transform_questions(questions)

                span.set_attribute(
                    SPANDATA.GEN_AI_INPUT_MESSAGES, json.dumps([input_message])
                )

            response = f(self, *args, **kwargs)

            if not isinstance(response, SystemOneResponse):
                return response

            span.set_attribute(SPANDATA.GEN_AI_RESPONSE_MODEL, response.model)

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

        with sentry_sdk.start_span(
            name=f"evaluate {model}".strip(),
            attributes={
                "sentry.op": OP.GEN_AI_EVALUATE,
                "sentry.origin": TypeSafeIntegration.origin,
                SPANDATA.GEN_AI_PROVIDER_NAME: "typesafe",
                SPANDATA.GEN_AI_OPERATION_NAME: "evaluate",
            },
        ) as span:
            if model is not None:
                span.set_attribute(SPANDATA.GEN_AI_REQUEST_MODEL, model)

            if client.options["data_collection"]["gen_ai"]["inputs"]:
                input_message: "InputMessageModel" = {
                    "type": "evaluation",
                }

                state = args[0] if len(args) > 0 else kwargs.get("state")
                if state is not None:
                    input_message["state"] = state

                questions = args[1] if len(args) > 1 else kwargs.get("questions")
                if isinstance(questions, Mapping):
                    input_message["questions"] = _transform_questions(questions)

                span.set_attribute(
                    SPANDATA.GEN_AI_INPUT_MESSAGES, json.dumps([input_message])
                )

            response = await f(self, *args, **kwargs)

            if not isinstance(response, SystemOneResponse):
                return response

            span.set_attribute(SPANDATA.GEN_AI_RESPONSE_MODEL, response.model)

            return response

    return wrap_system_one_async
