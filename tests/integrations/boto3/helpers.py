from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Mapping, Optional

import pytest

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3.consts import AWS_RPC_SYSTEM_NAME, ORIGIN

if TYPE_CHECKING:
    from botocore.client import BaseClient

    from sentry_sdk._types import SpanJSON


def require_botocore_model_fields(
    client: "BaseClient",
    method: str,
    input_fields: Iterable[str] = (),
    output_fields: Iterable[str] = (),
) -> None:
    # Operations and members can differ across the supported botocore models.
    operation_name = client.meta.method_to_api_mapping.get(method)
    if operation_name is None:
        pytest.skip("%s is absent from this botocore model" % method)
        return
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
    span: "SpanJSON",
    service: str,
    method: str,
    *,
    server_address: str,
    server_port: int = 443,
    region: str = "eu-north-1",
    name: Optional[str] = None,
    attributes: Optional[Mapping[str, Any]] = None,
) -> None:
    expected: Dict[str, Any] = {
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
    invoke_client_method: Callable[[], Any],
    capture_items: Callable[..., List[Any]],
    expected_origin: str = ORIGIN,
) -> Dict[Optional[str], List["SpanJSON"]]:
    items = capture_items("span")

    with sentry_sdk.start_span(name="parent"):
        invoke_client_method()

    sentry_sdk.flush()
    spans_by_op: Dict[Optional[str], List["SpanJSON"]] = {}
    for item in items:
        span = item.payload
        if span["attributes"].get(SPANDATA.SENTRY_ORIGIN) == expected_origin:
            spans_by_op.setdefault(
                span["attributes"].get(SPANDATA.SENTRY_OP), []
            ).append(span)
    return spans_by_op
