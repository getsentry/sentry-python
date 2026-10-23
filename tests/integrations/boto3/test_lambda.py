import base64
import io
import json
from contextlib import nullcontext
from copy import deepcopy

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import client_factory as client_factory


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


@pytest.mark.parametrize(
    "function_name,region,resource_id",
    [
        # fall back to client's region.
        pytest.param("billing:prod", "eu-north-1", None, id="name"),
        pytest.param(
            "arn:aws:lambda:us-east-1:123456789012:function:billing:7",
            "us-east-1",
            "arn:aws:lambda:us-east-1:123456789012:function:billing:7",
            id="arn",
        ),
    ],
)
def test_invoke_attributes(
    client_factory, capture_items, function_name, region, resource_id
):
    span, _ = _stubbed_span(
        client_factory,
        capture_items,
        "invoke",
        {"FunctionName": function_name},
        {"StatusCode": 200},
    )

    attributes = span["attributes"]
    assert attributes[SPANDATA.FAAS_INVOKED_NAME] == "billing"
    assert attributes[SPANDATA.FAAS_INVOKED_REGION] == region
    assert attributes.get(SPANDATA.CLOUD_RESOURCE_ID) == resource_id
    assert SPANDATA.AWS_LAMBDA_TARGET_FUNCTION_NAME not in attributes


def test_function_attributes(client_factory, capture_items):
    arn = "arn:aws:lambda:eu-north-1:123456789012:function:billing"
    span, _ = _stubbed_span(
        client_factory, capture_items, "delete_function", {"FunctionName": arn}, {}
    )

    attributes = span["attributes"]
    assert attributes[SPANDATA.AWS_LAMBDA_TARGET_FUNCTION_NAME] == "billing"
    assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == arn
    assert SPANDATA.FAAS_INVOKED_NAME not in attributes


@pytest.mark.parametrize(
    "method,params,response",
    [
        pytest.param(
            "create_event_source_mapping",
            {
                "FunctionName": "billing",
                "EventSourceArn": "arn:aws:sqs:eu-north-1:123456789012:orders",
            },
            {"UUID": "587b4f6c-1d0e-4d48-8a3f-2c6f9a1e0b7d"},
            id="from-response",
        ),
        pytest.param(
            "get_event_source_mapping",
            {"UUID": "587b4f6c-1d0e-4d48-8a3f-2c6f9a1e0b7d"},
            {},
            id="from-request",
        ),
    ],
)
def test_event_source_mapping_attributes(
    client_factory, capture_items, method, params, response
):
    span, _ = _stubbed_span(client_factory, capture_items, method, params, response)

    assert (
        span["attributes"][SPANDATA.AWS_LAMBDA_RESOURCE_MAPPING_ID]
        == "587b4f6c-1d0e-4d48-8a3f-2c6f9a1e0b7d"
    )


def test_invoke_function_error(client_factory, capture_items):
    span, _ = _stubbed_span(
        client_factory,
        capture_items,
        "invoke",
        {"FunctionName": "billing"},
        {
            "StatusCode": 200,
            "FunctionError": "Unhandled",
            "Payload": StreamingBody(io.BytesIO(b"{}"), 2),
        },
    )

    assert span["status"] == "error"
    assert span["attributes"][SPANDATA.ERROR_TYPE] == "lambda_function_error"


def test_invoke_propagates_trace(client_factory, capture_items):
    client_context = {
        "client": {"app_title": "shop"},
        "custom": {"customer_id": "1234567890"},
    }
    params = {
        "FunctionName": "billing",
        "ClientContext": base64.b64encode(json.dumps(client_context).encode()).decode(),
    }
    original_params = deepcopy(params)
    span, sent = _stubbed_span(
        client_factory, capture_items, "invoke", params, {"StatusCode": 200}
    )

    sent_context = json.loads(base64.b64decode(sent["ClientContext"]))
    custom = sent_context["custom"]
    assert custom.pop("sentry-trace") == f"{span['trace_id']}-{span['span_id']}-1"
    assert f"sentry-trace_id={span['trace_id']}" in custom.pop("baggage")
    # caller's values and params should be left unchanged.
    assert sent_context == client_context
    assert params == original_params


@pytest.mark.parametrize(
    "ignore_spans,parent",
    [
        pytest.param([], nullcontext, id="no-parent"),
        pytest.param(
            ["Lambda.Invoke"],
            lambda: sentry_sdk.start_span(name="parent"),
            id="ignored-span",
        ),
    ],
)
def test_invoke_propagates_scope_trace(
    sentry_init, client_factory, ignore_spans, parent
):
    client = client_factory("lambda")
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        ignore_spans=ignore_spans,
    )
    sent = []
    client.meta.events.register(
        "before-parameter-build", lambda params, **kwargs: sent.append(params)
    )
    with Stubber(client) as stubber:
        stubber.add_response("invoke", {"StatusCode": 200})
        with parent():
            client.invoke(FunctionName="billing")
            expected = dict(
                sentry_sdk.get_current_scope().iter_trace_propagation_headers()
            )

    # the scope's trace is propagated instead.
    (params,) = sent
    custom = json.loads(base64.b64decode(params["ClientContext"]))["custom"]
    assert custom["sentry-trace"] == expected["sentry-trace"]


@pytest.mark.parametrize(
    "params",
    [
        # lambda doesn't pass `ClientContext` to an async. invoked function.
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
