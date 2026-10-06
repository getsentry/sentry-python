import boto3
import pytest
from botocore.config import Config

from sentry_sdk.integrations.boto3 import Boto3Integration


@pytest.fixture
def client_factory(sentry_init, monkeypatch):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        default_integrations=False,
        server_name="",
    )
    # remove request retry delays without replacing botocore's retry handling.
    monkeypatch.setattr("botocore.endpoint.time.sleep", lambda delay: None)
    session = boto3.Session(
        aws_access_key_id="-",
        aws_secret_access_key="-",
        region_name="eu-north-1",
    )
    clients = []

    def make_client(service_name: str = "s3", attempt_count: int = 1, **client_kwargs):
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
