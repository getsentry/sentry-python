from datetime import datetime, timezone

import pytest
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import client_factory as client_factory

ARN_PREFIX = "arn:aws:states:eu-north-1:123456789012:"
STATE_MACHINE_ARN = ARN_PREFIX + "stateMachine:orders"
ALIAS_ARN = STATE_MACHINE_ARN + ":prod"
EXECUTION_ARN = ARN_PREFIX + "execution:orders:order-1234"
MAP_RUN_ARN = ARN_PREFIX + "mapRun:orders/process-items:5e7c"
ACTIVITY_ARN = ARN_PREFIX + "activity:approve"
DATE = datetime(2026, 10, 14, tzinfo=timezone.utc)


def _stubbed_span(client_factory, capture_items, method, params, response):
    client = client_factory("stepfunctions")
    require_botocore_model_fields(
        client, method, input_fields=tuple(params), output_fields=tuple(response)
    )
    with Stubber(client) as stubber:
        stubber.add_response(method, response, params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )
    (span,) = spans[OP.RPC]
    return span


@pytest.mark.parametrize(
    "method,params,response,expected",
    [
        pytest.param(
            "start_execution",
            {"stateMachineArn": ALIAS_ARN, "name": "order-1234"},
            {"executionArn": EXECUTION_ARN, "startDate": DATE},
            {
                SPANDATA.CLOUD_RESOURCE_ID: ALIAS_ARN,
                SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_ARN: ALIAS_ARN,
                SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME: "orders",
                SPANDATA.AWS_STEP_FUNCTIONS_EXECUTION_ARN: EXECUTION_ARN,
            },
            id="alias",
        ),
        pytest.param(
            "stop_execution",
            {"executionArn": EXECUTION_ARN},
            {"stopDate": DATE},
            {
                SPANDATA.CLOUD_RESOURCE_ID: EXECUTION_ARN,
                SPANDATA.AWS_STEP_FUNCTIONS_EXECUTION_ARN: EXECUTION_ARN,
                SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME: "orders",
            },
            id="execution",
        ),
        pytest.param(
            "list_executions",
            {"mapRunArn": MAP_RUN_ARN},
            {"executions": []},
            {
                SPANDATA.CLOUD_RESOURCE_ID: MAP_RUN_ARN,
                SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME: "orders",
            },
            id="map-run",
        ),
        pytest.param(
            "create_state_machine",
            {
                "name": "orders",
                "definition": "{}",
                "roleArn": "arn:aws:iam::123456789012:role/orders",
            },
            {"stateMachineArn": STATE_MACHINE_ARN, "creationDate": DATE},
            {
                SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_ARN: STATE_MACHINE_ARN,
                SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME: "orders",
            },
            id="create-state-machine",
        ),
        pytest.param(
            "create_activity",
            {"name": "approve"},
            {"activityArn": ACTIVITY_ARN, "creationDate": DATE},
            {
                SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_ARN: ACTIVITY_ARN,
                SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_NAME: "approve",
            },
            id="create-activity",
        ),
        pytest.param(
            "tag_resource",
            {"resourceArn": ACTIVITY_ARN, "tags": [{"key": "team", "value": "a"}]},
            {},
            {
                SPANDATA.CLOUD_RESOURCE_ID: ACTIVITY_ARN,
                SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_NAME: "approve",
            },
            id="tag-activity",
        ),
    ],
)
def test_resource_attributes(
    client_factory, capture_items, method, params, response, expected
):
    span = _stubbed_span(client_factory, capture_items, method, params, response)

    # `cloud.resource_id` is only set from the arn in the request the call acts on.
    assert {
        key: value
        for key, value in span["attributes"].items()
        if key.startswith("aws.step_functions.") or key == SPANDATA.CLOUD_RESOURCE_ID
    } == expected


@pytest.mark.parametrize(
    "method,status,expected_error_type",
    [
        ("start_sync_execution", "SUCCEEDED", None),
        ("start_sync_execution", "FAILED", "stepfunctions_execution_failed"),
        ("start_sync_execution", "TIMED_OUT", "stepfunctions_execution_timed_out"),
        ("describe_execution", "FAILED", None),
    ],
)
def test_execution_outcome(
    client_factory, capture_items, method, status, expected_error_type
):
    params = (
        {"stateMachineArn": STATE_MACHINE_ARN, "input": '{"card": "4242"}'}
        if method == "start_sync_execution"
        else {"executionArn": EXECUTION_ARN}
    )
    response = {
        "executionArn": EXECUTION_ARN,
        "stateMachineArn": STATE_MACHINE_ARN,
        "status": status,
        "startDate": DATE,
        "stopDate": DATE,
        "error": "States.TaskFailed",
        "cause": "card 4242 declined",
    }
    span = _stubbed_span(client_factory, capture_items, method, params, response)

    assert span["status"] == ("ok" if expected_error_type is None else "error")
    assert span["attributes"].get(SPANDATA.ERROR_TYPE) == expected_error_type
    # the execution's input, error and cause are never recorded.
    assert "4242" not in str(span)
    assert "States.TaskFailed" not in str(span)
