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
EXECUTION_ARN = ARN_PREFIX + "execution:orders:order-1234"
ACTIVITY_ARN = ARN_PREFIX + "activity:approve"
DATE = datetime(2026, 10, 14, tzinfo=timezone.utc)

STEP_FUNCTIONS_ATTRIBUTES = (
    SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_ARN,
    SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_NAME,
    SPANDATA.AWS_STEP_FUNCTIONS_EXECUTION_ARN,
    SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_ARN,
    SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME,
)


@pytest.fixture
def stepfunctions_client(client_factory):
    return client_factory("stepfunctions")


def _stubbed_span(client, capture_items, method, params, response):
    with Stubber(client) as stubber:
        stubber.add_response(method, response, params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )
    (span,) = spans[OP.RPC]
    return span


def test_request_attributes(client_factory, capture_items):
    client = client_factory("stepfunctions")
    state_machine_arn = "arn:aws:states:eu-north-1:123456789012:stateMachine:orders"

    with Stubber(client) as stubber:
        stubber.add_response(
            "delete_state_machine", {}, {"stateMachineArn": state_machine_arn}
        )
        spans = capture_spans_by_op(
            lambda: client.delete_state_machine(
                **{"stateMachineArn": state_machine_arn}
            ),
            capture_items,
        )

    (span,) = spans[OP.RPC]
    attributes = span["attributes"]
    assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
    assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == state_machine_arn


@pytest.mark.parametrize(
    "method,params,response,expected",
    [
        pytest.param(
            "start_execution",
            {"stateMachineArn": STATE_MACHINE_ARN + ":prod", "name": "order-1234"},
            {"executionArn": EXECUTION_ARN, "startDate": DATE},
            {
                SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_ARN: STATE_MACHINE_ARN
                + ":prod",
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
                SPANDATA.AWS_STEP_FUNCTIONS_EXECUTION_ARN: EXECUTION_ARN,
                SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME: "orders",
            },
            id="execution",
        ),
        pytest.param(
            "list_executions",
            {"mapRunArn": ARN_PREFIX + "mapRun:orders/process-items:5e7c"},
            {"executions": []},
            {SPANDATA.AWS_STEP_FUNCTIONS_STATE_MACHINE_NAME: "orders"},
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
            {SPANDATA.AWS_STEP_FUNCTIONS_ACTIVITY_NAME: "approve"},
            id="tag-activity",
        ),
    ],
)
def test_resource_attributes(
    stepfunctions_client, capture_items, method, params, response, expected
):
    require_botocore_model_fields(
        stepfunctions_client, method, input_fields=tuple(params)
    )
    span = _stubbed_span(stepfunctions_client, capture_items, method, params, response)

    attributes = span["attributes"]
    assert {
        key: value
        for key, value in attributes.items()
        if key in STEP_FUNCTIONS_ATTRIBUTES
    } == expected


@pytest.mark.parametrize(
    "method,status,expected_error_type",
    [
        ("start_sync_execution", "SUCCEEDED", None),
        ("start_sync_execution", "FAILED", "stepfunctions_execution_failed"),
        ("start_sync_execution", "TIMED_OUT", "stepfunctions_execution_timed_out"),
        # the status of an execution the call only describes.
        ("describe_execution", "FAILED", None),
    ],
)
def test_execution_outcome(
    stepfunctions_client, capture_items, method, status, expected_error_type
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
    require_botocore_model_fields(
        stepfunctions_client, method, output_fields=tuple(response)
    )
    span = _stubbed_span(stepfunctions_client, capture_items, method, params, response)

    attributes = span["attributes"]
    if expected_error_type is None:
        assert span["status"] == "ok"
        assert SPANDATA.ERROR_TYPE not in attributes
    else:
        assert span["status"] == "error"
        assert attributes[SPANDATA.ERROR_TYPE] == expected_error_type
    assert "4242" not in str(span)
    assert "States.TaskFailed" not in str(span)
