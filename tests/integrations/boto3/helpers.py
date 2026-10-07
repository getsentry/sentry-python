from typing import TYPE_CHECKING, Dict, List, Optional

import boto3
import pytest
from botocore.config import Config

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3.consts import ORIGIN

if TYPE_CHECKING:
    from sentry_sdk._types import SpanJSON


@pytest.fixture
def client_factory(sentry_init, monkeypatch):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
    )
    # remove request retry delays without replacing botocore's retry handling.
    monkeypatch.setattr("botocore.endpoint.time.sleep", lambda delay: None)
    session = boto3.Session(  # type: ignore
        aws_access_key_id="-",
        aws_secret_access_key="-",
        region_name="eu-north-1",
    )
    clients = []

    def make_client(service_name="s3", attempt_count=1, **client_kwargs):
        client = session.client(
            service_name,
            config=Config(
                retries={"total_max_attempts": attempt_count, "mode": "standard"}
            ),
            **client_kwargs,
        )  # type: ignore
        clients.append(client)
        return client

    yield make_client

    for client in clients:
        # older supported botocore versions do not expose `BaseClient.close()`.
        close = getattr(client, "close", None)
        if close is not None:
            close()


@pytest.fixture
def s3_client(client_factory):
    return client_factory("s3")


def require_botocore_model_fields(
    client,
    method,
    input_fields=(),
    output_fields=(),
):
    """Botocore models differ across versions. Skip tests if model is missing required fields."""
    operation_name = client.meta.method_to_api_mapping.get(method)
    if operation_name is None:
        pytest.skip("%s is absent from this botocore model; skipping test" % method)
    model = client.meta.service_model.operation_model(operation_name)  # type: ignore
    for shape, fields in (
        (model.input_shape, input_fields),
        (model.output_shape, output_fields),
    ):
        for field in fields:
            if shape is None or field not in shape.members:
                pytest.skip(
                    "%s.%s is absent from this botocore model; skipping test"
                    % (method, field)
                )


def capture_spans_by_op(
    invoke_client_method,
    capture_items,
    expected_origin=ORIGIN,
):
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
