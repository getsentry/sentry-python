import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import capture_spans_by_op
from tests.integrations.boto3.helpers import client_factory as client_factory

SECRET_ARN = "arn:aws:secretsmanager:eu-north-1:123456789012:secret:db-password-AbCdEf"


@pytest.fixture
def secretsmanager_client(client_factory):
    return client_factory("secretsmanager")


@pytest.mark.parametrize(
    "method,params,response,expected_arn,expected_name",
    [
        pytest.param(
            "get_secret_value",
            {"SecretId": "db-password"},
            {"ARN": SECRET_ARN, "Name": "db-password", "SecretString": "hunter2"},
            SECRET_ARN,
            "db-password",
            id="name",
        ),
        pytest.param(
            "get_secret_value",
            {"SecretId": SECRET_ARN},
            {"ARN": SECRET_ARN, "Name": "db-password", "SecretString": "hunter2"},
            SECRET_ARN,
            "db-password",
            id="arn",
        ),
        pytest.param(
            "create_secret",
            {"Name": "db-password", "SecretString": "hunter2"},
            {"ARN": SECRET_ARN, "Name": "db-password"},
            SECRET_ARN,
            "db-password",
            id="create",
        ),
        pytest.param(
            "tag_resource",
            {"SecretId": SECRET_ARN, "Tags": [{"Key": "team", "Value": "billing"}]},
            # doesn't return `ARN` or `Name`, so comes from request's `SecretId`.
            {},
            SECRET_ARN,
            "db-password",
            id="arn-without-response",
        ),
    ],
)
def test_secret_attributes(
    secretsmanager_client,
    capture_items,
    method,
    params,
    response,
    expected_arn,
    expected_name,
):
    with Stubber(secretsmanager_client) as stubber:
        stubber.add_response(method, response, params)
        spans = capture_spans_by_op(
            lambda: getattr(secretsmanager_client, method)(**params), capture_items
        )

    (span,) = spans[OP.RPC]
    operation = secretsmanager_client.meta.method_to_api_mapping[method]
    assert span["name"] == "SecretsManager." + operation
    attributes = span["attributes"]
    assert attributes[SPANDATA.AWS_SECRETSMANAGER_SECRET_ARN] == expected_arn
    assert attributes[SPANDATA.AWS_SECRETSMANAGER_SECRET_NAME] == expected_name
    # secret value is never recorded.
    assert "hunter2" not in str(span)


def test_client_error(secretsmanager_client, capture_items):
    params = {"SecretId": SECRET_ARN}

    with Stubber(secretsmanager_client) as stubber:
        stubber.add_client_error(
            "get_secret_value",
            service_error_code="ResourceNotFoundException",
            http_status_code=400,
            expected_params=params,
        )

        def call():
            with pytest.raises(ClientError):
                secretsmanager_client.get_secret_value(**params)

        spans = capture_spans_by_op(call, capture_items)

    (span,) = spans[OP.RPC]
    attributes = span["attributes"]
    assert span["status"] == "error"
    assert attributes[SPANDATA.ERROR_TYPE] == "ResourceNotFoundException"
    assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
    assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == SECRET_ARN
    # both from the request's `SecretId`.
    assert attributes[SPANDATA.AWS_SECRETSMANAGER_SECRET_ARN] == SECRET_ARN
    assert attributes[SPANDATA.AWS_SECRETSMANAGER_SECRET_NAME] == "db-password"
