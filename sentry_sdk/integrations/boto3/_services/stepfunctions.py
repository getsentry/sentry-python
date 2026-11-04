from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import (
    _extract_attributes,
    _get_aws_arn_attributes,
)
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any, Sequence

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.integrations.boto3._utils import _AttributeSpec

# set on both request and response if available.
_ARN_ATTRIBUTES: "Sequence[_AttributeSpec]" = (
    ("activityArn", SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_ARN),
    ("executionArn", SPANDATA.AWS_STEP_FUNCTIONS_EXECUTION_ARN),
    ("stateMachineArn", SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_ARN),
)


class _StepFunctionsExtension(_ServiceExtension):
    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = _extract_attributes(ctx.params, _ARN_ATTRIBUTES)

        # elsewhere `name` names an execution or an alias.
        if "name" in ctx.params:
            name = ctx.params["name"]
            if ctx.operation_name == "CreateActivity":
                attributes[SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_NAME] = name
            elif ctx.operation_name == "CreateStateMachine":
                attributes[SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME] = name

        for param in (
            "activityArn",
            "executionArn",
            "mapRunArn",
            "resourceArn",
            "stateMachineAliasArn",
            "stateMachineArn",
            "stateMachineVersionArn",
        ):
            if param in ctx.params:
                arn = ctx.params[param]
                break
        else:
            return attributes

        # params aren't validated yet by botocore.
        with capture_internal_exceptions():
            arn_attributes = _get_aws_arn_attributes((arn,), ("states",))
            attributes.update(arn_attributes)
            if arn_attributes:
                # `arn:<partition>:states:<region>:<account>:<type>:<name>…`; the name
                # ends at the next ":" (version, alias, execution) or "/" (map run).
                resource_type, resource = arn.split(":", 6)[5:]
                name = resource.split(":", 1)[0].split("/", 1)[0]
                if resource_type == "activity":
                    attributes[SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_NAME] = name
                else:
                    attributes[SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME] = name

        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        attributes = _extract_attributes(response, _ARN_ATTRIBUTES)

        # `StartSyncExecution` returns `200 OK` even for failed/timed out executions.
        # https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartSyncExecution.html
        if ctx.operation_name == "StartSyncExecution":
            if response.get("status") == "FAILED":
                attributes[SPANDATA.ERROR_TYPE] = "stepfunctions_execution_failed"
            elif response.get("status") == "TIMED_OUT":
                attributes[SPANDATA.ERROR_TYPE] = "stepfunctions_execution_timed_out"

        return attributes
