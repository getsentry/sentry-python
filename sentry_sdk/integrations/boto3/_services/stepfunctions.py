from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import (
    _extract_attributes,
    _get_aws_arn_attributes,
)
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Sequence, Tuple

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.integrations.boto3._utils import _AttributeSpec

# the arns otel records from the request and the response, plus the execution's.
# https://opentelemetry.io/docs/specs/semconv/registry/attributes/aws/#amazon-step-functions-attributes
_ARN_ATTRIBUTES: "Sequence[_AttributeSpec]" = (
    ("activityArn", SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_ARN),
    ("executionArn", SPANDATA.AWS_STEP_FUNCTIONS_EXECUTION_ARN),
    ("stateMachineArn", SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_ARN),
)

# request parameters with the arn of the resource a call acts on. a call takes at most
# one, `ListExecutions` either a `mapRunArn` or a `stateMachineArn`.
_TARGET_ARN_PARAMS = (
    "activityArn",
    "executionArn",
    "mapRunArn",
    "resourceArn",
    "stateMachineAliasArn",
    "stateMachineArn",
    "stateMachineVersionArn",
)

# apis whose `name` is the name of what they create; elsewhere it names an execution
# (often per order or customer) or an alias.
_CREATED_NAME_ATTRIBUTES = {
    "CreateActivity": SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_NAME,
    "CreateStateMachine": SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME,
}

# `StartSyncExecution` returns `200 OK` for a failed or timed out execution too.
# https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartSyncExecution.html
_SYNC_EXECUTION_ERROR_TYPES = {
    "FAILED": "stepfunctions_execution_failed",
    "TIMED_OUT": "stepfunctions_execution_timed_out",
}


def _get_name_attribute(arn: str) -> "Tuple[str, str]":
    # `arn:<partition>:states:<region>:<account>:<type>:<name>…`, where <name> is the
    # activity's or the state machine's. versions and aliases append `:<qualifier>`,
    # executions `:<execution name>`; map runs and their child executions put `/<label>`
    # after it. names can't contain ":" or "/".
    # https://docs.aws.amazon.com/step-functions/latest/dg/concepts-state-machine-version.html
    # https://docs.aws.amazon.com/step-functions/latest/dg/concepts-examine-map-run.html
    # https://docs.aws.amazon.com/step-functions/latest/apireference/API_CreateStateMachine.html#StepFunctions-CreateStateMachine-request-name
    parts = arn.split(":", 7)
    if parts[5] == "activity":
        return SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_NAME, parts[6]
    return SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME, parts[6].split("/", 1)[0]


class _StepFunctionsExtension(_ServiceExtension):
    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = _extract_attributes(ctx.params, _ARN_ATTRIBUTES)
        with capture_internal_exceptions():
            name_attribute = _CREATED_NAME_ATTRIBUTES.get(ctx.operation_name)
            if name_attribute is not None:
                attributes[name_attribute] = ctx.params["name"]

            for param in _TARGET_ARN_PARAMS:
                if param in ctx.params:
                    arn = ctx.params[param]
                    attributes.update(_get_aws_arn_attributes((arn,), ("states",)))
                    name_attribute, name = _get_name_attribute(arn)
                    attributes[name_attribute] = name
                    break
        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        # never `input`, `output`, `error` or `cause`.
        attributes = _extract_attributes(response, _ARN_ATTRIBUTES)
        # only `StartSyncExecution` runs the execution its `status` describes; a failed
        # call has none.
        if (
            ctx.operation_name == "StartSyncExecution"
            and response.get("status") in _SYNC_EXECUTION_ERROR_TYPES
        ):
            attributes[SPANDATA.ERROR_TYPE] = _SYNC_EXECUTION_ERROR_TYPES[
                response["status"]
            ]
        return attributes
