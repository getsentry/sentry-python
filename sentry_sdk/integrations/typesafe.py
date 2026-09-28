from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import get_start_span_function
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.tracing_utils import has_span_streaming_enabled

if TYPE_CHECKING:
    from typing import Any, Callable

try:
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

            response = f(self, *args, **kwargs)

            if not isinstance(response, SystemOneResponse):
                return response

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

            response = await f(self, *args, **kwargs)

            if not isinstance(response, SystemOneResponse):
                return response

            return response

    return wrap_system_one_async
