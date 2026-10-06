import pytest

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3.consts import AWS_RPC_SYSTEM_NAME, ORIGIN


def require_botocore_model_fields(client, method, input_fields=(), output_fields=()):
    # Operations and members can differ across the supported botocore models.
    operation_name = client.meta.method_to_api_mapping.get(method)
    if operation_name is None:
        pytest.skip("%s is absent from this botocore model" % method)
    model = client.meta.service_model.operation_model(operation_name)
    for shape, fields in (
        (model.input_shape, input_fields),
        (model.output_shape, output_fields),
    ):
        for field in fields:
            if shape is None or field not in shape.members:
                pytest.skip(
                    "%s.%s is absent from this botocore model" % (method, field)
                )


def assert_client_span(
    span,
    service,
    method,
    *,
    server_address,
    server_port=443,
    region="eu-north-1",
    name=None,
    attributes=None,
):
    expected = {
        SPANDATA.SENTRY_OP: OP.HTTP_CLIENT,
        SPANDATA.SENTRY_ORIGIN: ORIGIN,
        SPANDATA.SENTRY_KIND: "client",
        SPANDATA.CLOUD_PROVIDER: "aws",
        SPANDATA.RPC_SYSTEM_NAME: AWS_RPC_SYSTEM_NAME,
        SPANDATA.RPC_SERVICE: service,
        SPANDATA.RPC_METHOD: method,
        SPANDATA.CLOUD_REGION: region,
        SPANDATA.SERVER_ADDRESS: server_address,
        SPANDATA.SERVER_PORT: server_port,
    }
    expected.update(attributes or {})
    assert span["name"] == (name if name is not None else "%s.%s" % (service, method))
    assert span["end_timestamp"] is not None
    for key, value in expected.items():
        assert span["attributes"][key] == value, key


def capture_spans_by_op(
    invoke_client_method, capture_items, expected_origin=ORIGIN
):
    items = capture_items("span")

    with sentry_sdk.start_span(name="parent"):
        invoke_client_method()

    sentry_sdk.flush()
    spans_by_op = {}
    for item in items:
        span = item.payload
        if span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == expected_origin:
            spans_by_op.setdefault(
                span["attributes"].get(SPANDATA.SENTRY_OP), []
            ).append(span)
    return spans_by_op
