import base64
import io
import json
from copy import deepcopy

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import client_factory as client_factory

FUNCTION_ARN = "arn:aws:lambda:eu-north-1:123456789012:function:billing"
MAPPING_ID = "587b4f6c-1d0e-4d48-8a3f-2c6f9a1e0b7d"


def _stubbed_span(client_factory, capture_items, method, params, response):
    """Return the span and the params botocore sent."""
    client = client_factory("lambda")
    require_botocore_model_fields(
        client, method, input_fields=tuple(params), output_fields=tuple(response)
    )
    sent = []
    client.meta.events.register(
        "before-parameter-build", lambda params, **kwargs: sent.append(params)
    )
    with Stubber(client) as stubber:
        stubber.add_response(method, response)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )
    (span,) = spans[OP.RPC]
    return span, sent[0]


def _client_context(value):
    return json.loads(base64.b64decode(value))


@pytest.mark.parametrize(
    "method,params,response,expected",
    [
        pytest.param(
            "invoke",
            {"FunctionName": "billing:prod"},
            {"StatusCode": 200},
            {
                SPANDATA.FAAS_INVOKED_NAME: "billing",
                SPANDATA.FAAS_INVOKED_PROVIDER: "aws",
                SPANDATA.FAAS_INVOKED_REGION: "eu-north-1",
            },
            id="invoke-name",
        ),
        pytest.param(
            "invoke",
            {
                "FunctionName": "arn:aws:lambda:us-east-1:123456789012:function:billing:7"
            },
            {"StatusCode": 200},
            {
                SPANDATA.CLOUD_RESOURCE_ID: "arn:aws:lambda:us-east-1:123456789012:function:billing:7",
                SPANDATA.FAAS_INVOKED_NAME: "billing",
                SPANDATA.FAAS_INVOKED_PROVIDER: "aws",
                SPANDATA.FAAS_INVOKED_REGION: "us-east-1",
            },
            id="invoke-arn",
        ),
        pytest.param(
            "delete_function",
            {"FunctionName": FUNCTION_ARN},
            {},
            {
                SPANDATA.CLOUD_RESOURCE_ID: FUNCTION_ARN,
                SPANDATA.AWS_LAMBDA_TARGET_FUNCTION_NAME: "billing",
            },
            id="function-arn",
        ),
        pytest.param(
            "create_event_source_mapping",
            {
                "FunctionName": "billing",
                "EventSourceArn": "arn:aws:sqs:eu-north-1:123456789012:orders",
            },
            {"UUID": MAPPING_ID},
            {
                SPANDATA.AWS_LAMBDA_TARGET_FUNCTION_NAME: "billing",
                SPANDATA.AWS_LAMBDA_RESOURCE_MAPPING_ID: MAPPING_ID,
            },
            id="mapping-from-response",
        ),
        pytest.param(
            "get_event_source_mapping",
            {"UUID": MAPPING_ID},
            {},
            {SPANDATA.AWS_LAMBDA_RESOURCE_MAPPING_ID: MAPPING_ID},
            id="mapping-from-request",
        ),
    ],
)
def test_function_attributes(
    client_factory, capture_items, method, params, response, expected
):
    span, _ = _stubbed_span(client_factory, capture_items, method, params, response)

    assert {
        key: value
        for key, value in span["attributes"].items()
        if key.startswith(("faas.invoked_", "aws.lambda."))
        or key == SPANDATA.CLOUD_RESOURCE_ID
    } == expected


@pytest.mark.parametrize(
    "response,expected_error_type",
    [
        pytest.param({"StatusCode": 200}, None, id="ok"),
        pytest.param(
            {"StatusCode": 200, "FunctionError": "Unhandled"},
            "lambda_function_error",
            id="function-error",
        ),
        # the payload is never read; the span still ends with the call.
        pytest.param(
            {"StatusCode": 200, "Payload": StreamingBody(io.BytesIO(b"null"), 4)},
            None,
            id="unread-payload",
        ),
    ],
)
def test_invoke_outcome(client_factory, capture_items, response, expected_error_type):
    span, _ = _stubbed_span(
        client_factory, capture_items, "invoke", {"FunctionName": "billing"}, response
    )

    assert span["status"] == ("ok" if expected_error_type is None else "error")
    assert span["attributes"].get(SPANDATA.ERROR_TYPE) == expected_error_type


def test_invoke_propagates_trace(client_factory, capture_items):
    span, sent = _stubbed_span(
        client_factory,
        capture_items,
        "invoke",
        {"FunctionName": "billing"},
        {"StatusCode": 200},
    )

    custom = _client_context(sent["ClientContext"])["custom"]
    assert custom["sentry-trace"] == f"{span['trace_id']}-{span['span_id']}-1"
    assert f"sentry-trace_id={span['trace_id']}" in custom["baggage"]


def test_invoke_keeps_client_context(client_factory, capture_items):
    client_context = {
        "client": {"app_title": "shop"},
        "custom": {"tenant": "a"},
    }
    params = {
        "FunctionName": "billing",
        "ClientContext": base64.b64encode(json.dumps(client_context).encode()).decode(),
    }
    original = deepcopy(params)
    _, sent = _stubbed_span(
        client_factory, capture_items, "invoke", params, {"StatusCode": 200}
    )

    # only the trace data is added; the caller's values and params are unchanged.
    assert params == original
    sent_context = _client_context(sent["ClientContext"])
    del sent_context["custom"]["sentry-trace"], sent_context["custom"]["baggage"]
    assert sent_context == client_context


def test_invoke_propagates_without_span(client_factory):
    client = client_factory("lambda")
    sent = []
    client.meta.events.register(
        "before-parameter-build", lambda params, **kwargs: sent.append(params)
    )
    with Stubber(client) as stubber:
        stubber.add_response("invoke", {"StatusCode": 200})
        client.invoke(FunctionName="billing")

    # without a parent, no span is started; the scope's trace is propagated instead,
    # like the HTTP headers.
    (params,) = sent
    trace_id = sentry_sdk.get_current_scope().get_active_propagation_context().trace_id
    assert _client_context(params["ClientContext"])["custom"][
        "sentry-trace"
    ].startswith(trace_id)


@pytest.mark.parametrize(
    "params",
    [
        # lambda doesn't pass `ClientContext` to an asynchronously invoked function.
        pytest.param(
            {"FunctionName": "billing", "InvocationType": "Event"}, id="async"
        ),
        # with trace data, it would exceed the 3,583 bytes lambda accepts.
        pytest.param(
            {
                "FunctionName": "billing",
                "ClientContext": base64.b64encode(
                    json.dumps({"custom": {"padding": "x" * 2600}}).encode()
                ).decode(),
            },
            id="too-large",
        ),
        # the caller propagates its own trace.
        pytest.param(
            {
                "FunctionName": "billing",
                "ClientContext": base64.b64encode(
                    json.dumps({"custom": {"sentry-trace": "set-by-caller"}}).encode()
                ).decode(),
            },
            id="caller-trace",
        ),
    ],
)
def test_invoke_without_propagation(client_factory, capture_items, params):
    _, sent = _stubbed_span(
        client_factory, capture_items, "invoke", params, {"StatusCode": 202}
    )

    assert sent == params
