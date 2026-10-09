import json
import re
import sys
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations.aws_lambda._handler import _wrap_handler
from sentry_sdk.integrations.aws_lambda.consts import IDENTIFIER
from sentry_sdk.utils import (
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    logger,
)

if TYPE_CHECKING:
    from typing import Any, Callable, Optional, TypeVar

    from sentry_sdk._types import Event

    F = TypeVar("F", bound=Callable[..., Any])


def _wrap_init_error(init_error: "F") -> "F":
    from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

    @ensure_integration_enabled(AwsLambdaIntegration, init_error)
    def sentry_init_error(*args: "Any", **kwargs: "Any") -> "Any":
        client = sentry_sdk.get_client()

        with capture_internal_exceptions():
            sentry_sdk.get_isolation_scope().clear_breadcrumbs()

            exc_info = sys.exc_info()
            if exc_info and all(exc_info):
                sentry_event, hint = event_from_exception(
                    exc_info,
                    client_options=client.options,
                    mechanism={"type": IDENTIFIER, "handled": False},
                )
                sentry_sdk.capture_event(sentry_event, hint=hint)

            else:
                # Fall back to AWS lambdas JSON representation of the error
                error_info = args[1]
                if isinstance(error_info, str):
                    error_info = json.loads(error_info)
                sentry_event = _event_from_error_json(error_info)
                sentry_sdk.capture_event(sentry_event)

        return init_error(*args, **kwargs)

    return sentry_init_error  # type: ignore


def _drain_queue() -> None:
    with capture_internal_exceptions():
        client = sentry_sdk.get_client()
        integration = client.get_integration(IDENTIFIER)
        if integration is not None:
            # Flush out the event queue before AWS kills the
            # process.
            client.flush()


def _setup_once() -> None:
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

    pre_37 = hasattr(lambda_bootstrap, "handle_http_request")  # Python 3.6

    if pre_37:
        old_handle_event_request = lambda_bootstrap.handle_event_request

        def sentry_handle_event_request(
            request_handler: "Any", *args: "Any", **kwargs: "Any"
        ) -> "Any":
            request_handler = _wrap_handler(request_handler)
            return old_handle_event_request(request_handler, *args, **kwargs)

        lambda_bootstrap.handle_event_request = sentry_handle_event_request

        old_handle_http_request = lambda_bootstrap.handle_http_request

        def sentry_handle_http_request(
            request_handler: "Any", *args: "Any", **kwargs: "Any"
        ) -> "Any":
            request_handler = _wrap_handler(request_handler)
            return old_handle_http_request(request_handler, *args, **kwargs)

        lambda_bootstrap.handle_http_request = sentry_handle_http_request

        # Patch to_json to drain the queue. This should work even when the
        # SDK is initialized inside of the handler

        old_to_json = lambda_bootstrap.to_json

        def sentry_to_json(*args: "Any", **kwargs: "Any") -> "Any":
            _drain_queue()
            return old_to_json(*args, **kwargs)

        lambda_bootstrap.to_json = sentry_to_json
    else:
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


def get_lambda_bootstrap() -> "Optional[Any]":
    # Python 3.7: If the bootstrap module is *already imported*, it is the
    # one we actually want to use (no idea what's in __main__)
    #
    # Python 3.8: bootstrap is also importable, but will be the same file
    # as __main__ imported under a different name:
    #
    #     sys.modules['__main__'].__file__ == sys.modules['bootstrap'].__file__
    #     sys.modules['__main__'] is not sys.modules['bootstrap']
    #
    # Python 3.9: bootstrap is in __main__.awslambdaricmain
    #
    # On container builds using the `aws-lambda-python-runtime-interface-client`
    # (awslamdaric) module, bootstrap is located in sys.modules['__main__'].bootstrap
    #
    # Such a setup would then make all monkeypatches useless.
    if "bootstrap" in sys.modules:
        return sys.modules["bootstrap"]
    elif "__main__" in sys.modules:
        module = sys.modules["__main__"]
        # python3.9 runtime
        if hasattr(module, "awslambdaricmain") and hasattr(
            module.awslambdaricmain, "bootstrap"
        ):
            return module.awslambdaricmain.bootstrap
        elif hasattr(module, "bootstrap"):
            # awslambdaric python module in container builds
            return module.bootstrap

        # python3.8 runtime
        return module
    else:
        return None


def _parse_formatted_traceback(formatted_tb: "list[str]") -> "list[dict[str, Any]]":
    frames = []
    for frame in formatted_tb:
        match = re.match(r'File "(.+)", line (\d+), in (.+)', frame.strip())
        if match:
            file_name, line_number, func_name = match.groups()
            line_number = int(line_number)
            frames.append(
                {
                    "filename": file_name,
                    "function": func_name,
                    "lineno": line_number,
                    "vars": None,
                    "pre_context": None,
                    "context_line": None,
                    "post_context": None,
                }
            )
    return frames


def _event_from_error_json(error_json: "dict[str, Any]") -> "Event":
    """
    Converts the error JSON from AWS Lambda into a Sentry error event.
    This is not a full fletched event, but better than nothing.

    This is an example of where AWS creates the error JSON:
    https://github.com/aws/aws-lambda-python-runtime-interface-client/blob/2.2.1/awslambdaric/bootstrap.py#L479
    """
    event: "Event" = {
        "level": "error",
        "exception": {
            "values": [
                {
                    "type": error_json.get("errorType"),
                    "value": error_json.get("errorMessage"),
                    "stacktrace": {
                        "frames": _parse_formatted_traceback(
                            error_json.get("stackTrace", [])
                        ),
                    },
                    "mechanism": {
                        "type": IDENTIFIER,
                        "handled": False,
                    },
                }
            ],
        },
    }

    return event
