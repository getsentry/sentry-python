from typing import TYPE_CHECKING

from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.aws_lambda._handler import _wrap_handler
from sentry_sdk.integrations.aws_lambda._runtime import (
    _drain_queue,
    _wrap_init_error,
    get_lambda_bootstrap,
)
from sentry_sdk.integrations.aws_lambda.consts import IDENTIFIER, ORIGIN
from sentry_sdk.utils import logger

if TYPE_CHECKING:
    from typing import Any, Callable, TypeVar

    F = TypeVar("F", bound=Callable[..., Any])


class AwsLambdaIntegration(Integration):
    identifier = IDENTIFIER
    origin = ORIGIN

    def __init__(self, timeout_warning: bool = False) -> None:
        self.timeout_warning = timeout_warning

    @staticmethod
    def setup_once() -> None:
        lambda_bootstrap = get_lambda_bootstrap()
        if not lambda_bootstrap:
            logger.warning(
                "Not running in AWS Lambda environment, "
                "AwsLambdaIntegration disabled (could not find bootstrap module)"
            )
            return

        if not hasattr(lambda_bootstrap, "handle_event_request"):
            logger.warning(
                "Not running in AWS Lambda environment, "
                "AwsLambdaIntegration disabled (could not find handle_event_request)"
            )
            return

        lambda_bootstrap.LambdaRuntimeClient.post_init_error = _wrap_init_error(
            lambda_bootstrap.LambdaRuntimeClient.post_init_error
        )

        old_handle_event_request = lambda_bootstrap.handle_event_request

        def sentry_handle_event_request(  # type: ignore
            lambda_runtime_client, request_handler, *args, **kwargs
        ):
            request_handler = _wrap_handler(request_handler)
            return old_handle_event_request(
                lambda_runtime_client, request_handler, *args, **kwargs
            )

        lambda_bootstrap.handle_event_request = sentry_handle_event_request

        # Patch the runtime client to drain the queue. This should work
        # even when the SDK is initialized inside of the handler

        def _wrap_post_function(f: "F") -> "F":
            def inner(*args: "Any", **kwargs: "Any") -> "Any":
                _drain_queue()
                return f(*args, **kwargs)

            return inner  # type: ignore

        lambda_bootstrap.LambdaRuntimeClient.post_invocation_result = (
            _wrap_post_function(
                lambda_bootstrap.LambdaRuntimeClient.post_invocation_result
            )
        )
        lambda_bootstrap.LambdaRuntimeClient.post_invocation_error = (
            _wrap_post_function(
                lambda_bootstrap.LambdaRuntimeClient.post_invocation_error
            )
        )
