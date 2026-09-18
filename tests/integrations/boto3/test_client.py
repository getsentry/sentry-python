from sentry_sdk.integrations.boto3 import Boto3Integration


def test_public_api():
    assert Boto3Integration.__module__ == "sentry_sdk.integrations.boto3"
    assert Boto3Integration.identifier == "boto3"
