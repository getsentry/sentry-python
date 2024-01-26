import json

import pytest

try:
    from unittest import mock  # python 3.3 and above
    from unittest.mock import MagicMock
except ImportError:
    import mock  # python < 3.3
    from mock import MagicMock

from sentry_sdk.integrations.cloud_resource_context import (
    CLOUD_PLATFORM,
    CLOUD_PROVIDER,
)

AWS_EC2_EXAMPLE_IMDSv2_PAYLOAD = {
    "accountId": "298817902971",
    "architecture": "x86_64",
    "availabilityZone": "us-east-1b",
    "billingProducts": None,
    "devpayProductCodes": None,
    "marketplaceProductCodes": None,
    "imageId": "ami-00874d747dde344fa",
    "instanceId": "i-07d3301297fe0a55a",
    "instanceType": "t2.small",
    "kernelId": None,
    "pendingTime": "2023-02-08T07:54:05Z",
    "privateIp": "171.131.65.115",
    "ramdiskId": None,
    "region": "us-east-1",
    "version": "2017-09-30",
}

try:
    # Python 3
    AWS_EC2_EXAMPLE_IMDSv2_PAYLOAD_BYTES = bytes(
        json.dumps(AWS_EC2_EXAMPLE_IMDSv2_PAYLOAD), "utf-8"
    )
except TypeError:
    # Python 2
    AWS_EC2_EXAMPLE_IMDSv2_PAYLOAD_BYTES = bytes(
        json.dumps(AWS_EC2_EXAMPLE_IMDSv2_PAYLOAD)
    ).encode("utf-8")

GCP_GCE_EXAMPLE_METADATA_PLAYLOAD = {
    "instance": {
        "attributes": {},
        "cpuPlatform": "Intel Broadwell",
        "description": "",
        "disks": [
            {
                "deviceName": "tests-cloud-contexts-in-python-sdk",
                "index": 0,
                "interface": "SCSI",
                "mode": "READ_WRITE",
                "type": "PERSISTENT-BALANCED",
            }
        ],
        "guestAttributes": {},
        "hostname": "tests-cloud-contexts-in-python-sdk.c.client-infra-internal.internal",
        "id": 1535324527892303790,
        "image": "projects/debian-cloud/global/images/debian-11-bullseye-v20221206",
        "licenses": [{"id": "2853224013536823851"}],
        "machineType": "projects/542054129475/machineTypes/e2-medium",
        "maintenanceEvent": "NONE",
        "name": "tests-cloud-contexts-in-python-sdk",
        "networkInterfaces": [
            {
                "accessConfigs": [
                    {"externalIp": "134.30.53.15", "type": "ONE_TO_ONE_NAT"}
                ],
                "dnsServers": ["169.254.169.254"],
                "forwardedIps": [],
                "gateway": "10.188.0.1",
                "ip": "10.188.0.3",
                "ipAliases": [],
                "mac": "42:01:0c:7c:00:13",
                "mtu": 1460,
                "network": "projects/544954029479/networks/default",
                "subnetmask": "255.255.240.0",
                "targetInstanceIps": [],
            }
        ],
        "preempted": "FALSE",
        "remainingCpuTime": -1,
        "scheduling": {
            "automaticRestart": "TRUE",
            "onHostMaintenance": "MIGRATE",
            "preemptible": "FALSE",
        },
        "serviceAccounts": {},
        "tags": ["http-server", "https-server"],
        "virtualClock": {"driftToken": "0"},
        "zone": "projects/142954069479/zones/northamerica-northeast2-b",
    },
    "oslogin": {"authenticate": {"sessions": {}}},
    "project": {
        "attributes": {},
        "numericProjectId": 204954049439,
        "projectId": "my-project-internal",
    },
}

try:
    # Python 3
    GCP_GCE_EXAMPLE_METADATA_PLAYLOAD_BYTES = bytes(
        json.dumps(GCP_GCE_EXAMPLE_METADATA_PLAYLOAD), "utf-8"
    )
except TypeError:
    # Python 2
    GCP_GCE_EXAMPLE_METADATA_PLAYLOAD_BYTES = bytes(
        json.dumps(GCP_GCE_EXAMPLE_METADATA_PLAYLOAD)
    ).encode("utf-8")


def test_is_aws_http_error():
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    response = MagicMock()
    response.status = 405

    CloudResourceContextIntegration.http = MagicMock()
    CloudResourceContextIntegration.http.request = MagicMock(return_value=response)

    assert CloudResourceContextIntegration._is_aws() is False
    assert CloudResourceContextIntegration.aws_token == ""


def test_is_aws_ok():
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    response = MagicMock()
    response.status = 200
    response.data = b"something"
    CloudResourceContextIntegration.http = MagicMock()
    CloudResourceContextIntegration.http.request = MagicMock(return_value=response)

    assert CloudResourceContextIntegration._is_aws() is True
    assert CloudResourceContextIntegration.aws_token == "something"

    CloudResourceContextIntegration.http.request = MagicMock(
        side_effect=Exception("Test")
    )
    assert CloudResourceContextIntegration._is_aws() is False


def test_is_aw_exception():
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    CloudResourceContextIntegration.http = MagicMock()
    CloudResourceContextIntegration.http.request = MagicMock(
        side_effect=Exception("Test")
    )

    assert CloudResourceContextIntegration._is_aws() is False


@pytest.mark.parametrize(
    "http_status, response_data, expected_context",
    [
        [
            405,
            b"",
            {
                "cloud.provider": CLOUD_PROVIDER.AWS,
                "cloud.platform": CLOUD_PLATFORM.AWS_EC2,
            },
        ],
        [
            200,
            b"something-but-not-json",
            {
                "cloud.provider": CLOUD_PROVIDER.AWS,
                "cloud.platform": CLOUD_PLATFORM.AWS_EC2,
            },
        ],
        [
            200,
            AWS_EC2_EXAMPLE_IMDSv2_PAYLOAD_BYTES,
            {
                "cloud.provider": "aws",
                "cloud.platform": "aws_ec2",
                "cloud.account.id": "298817902971",
                "cloud.availability_zone": "us-east-1b",
                "cloud.region": "us-east-1",
                "host.id": "i-07d3301297fe0a55a",
                "host.type": "t2.small",
            },
        ],
    ],
)
def test_get_aws_context(http_status, response_data, expected_context):
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    response = MagicMock()
    response.status = http_status
    response.data = response_data

    CloudResourceContextIntegration.http = MagicMock()
    CloudResourceContextIntegration.http.request = MagicMock(return_value=response)

    assert CloudResourceContextIntegration._get_aws_context() == expected_context


def test_is_gcp_http_error():
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    response = MagicMock()
    response.status = 405
    response.data = b'{"some": "json"}'
    CloudResourceContextIntegration.http = MagicMock()
    CloudResourceContextIntegration.http.request = MagicMock(return_value=response)

    assert CloudResourceContextIntegration._is_gcp() is False
    assert CloudResourceContextIntegration.gcp_metadata is None


def test_is_gcp_ok():
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    response = MagicMock()
    response.status = 200
    response.data = b'{"some": "json"}'
    CloudResourceContextIntegration.http = MagicMock()
    CloudResourceContextIntegration.http.request = MagicMock(return_value=response)

    assert CloudResourceContextIntegration._is_gcp() is True
    assert CloudResourceContextIntegration.gcp_metadata == {"some": "json"}


def test_is_gcp_exception():
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    CloudResourceContextIntegration.http = MagicMock()
    CloudResourceContextIntegration.http.request = MagicMock(
        side_effect=Exception("Test")
    )
    assert CloudResourceContextIntegration._is_gcp() is False


@pytest.mark.parametrize(
    "http_status, response_data, expected_context",
    [
        [
            405,
            None,
            {
                "cloud.provider": CLOUD_PROVIDER.GCP,
                "cloud.platform": CLOUD_PLATFORM.GCP_COMPUTE_ENGINE,
            },
        ],
        [
            200,
            b"something-but-not-json",
            {
                "cloud.provider": CLOUD_PROVIDER.GCP,
                "cloud.platform": CLOUD_PLATFORM.GCP_COMPUTE_ENGINE,
            },
        ],
        [
            200,
            GCP_GCE_EXAMPLE_METADATA_PLAYLOAD_BYTES,
            {
                "cloud.provider": "gcp",
                "cloud.platform": "gcp_compute_engine",
                "cloud.account.id": "my-project-internal",
                "cloud.availability_zone": "northamerica-northeast2-b",
                "host.id": 1535324527892303790,
            },
        ],
    ],
)
def test_get_gcp_context(http_status, response_data, expected_context):
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    CloudResourceContextIntegration.gcp_metadata = None

    response = MagicMock()
    response.status = http_status
    response.data = response_data

    CloudResourceContextIntegration.http = MagicMock()
    CloudResourceContextIntegration.http.request = MagicMock(return_value=response)

    assert CloudResourceContextIntegration._get_gcp_context() == expected_context


@pytest.mark.parametrize(
    "is_aws, is_gcp, expected_provider",
    [
        [False, False, ""],
        [False, True, CLOUD_PROVIDER.GCP],
        [True, False, CLOUD_PROVIDER.AWS],
        [True, True, CLOUD_PROVIDER.AWS],
    ],
)
def test_get_cloud_provider(is_aws, is_gcp, expected_provider):
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    CloudResourceContextIntegration._is_aws = MagicMock(return_value=is_aws)
    CloudResourceContextIntegration._is_gcp = MagicMock(return_value=is_gcp)

    assert CloudResourceContextIntegration._get_cloud_provider() == expected_provider


@pytest.mark.parametrize(
    "cloud_provider",
    [
        CLOUD_PROVIDER.ALIBABA,
        CLOUD_PROVIDER.AZURE,
        CLOUD_PROVIDER.IBM,
        CLOUD_PROVIDER.TENCENT,
    ],
)
def test_get_cloud_resource_context_unsupported_providers(cloud_provider):
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    CloudResourceContextIntegration._get_cloud_provider = MagicMock(
        return_value=cloud_provider
    )

    assert CloudResourceContextIntegration._get_cloud_resource_context() == {}


@pytest.mark.parametrize(
    "cloud_provider",
    [
        CLOUD_PROVIDER.AWS,
        CLOUD_PROVIDER.GCP,
    ],
)
def test_get_cloud_resource_context_supported_providers(cloud_provider):
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    CloudResourceContextIntegration._get_cloud_provider = MagicMock(
        return_value=cloud_provider
    )

    assert CloudResourceContextIntegration._get_cloud_resource_context() != {}


@pytest.mark.parametrize(
    "cloud_provider, cloud_resource_context, warning_called, set_context_called",
    [
        ["", {}, False, False],
        [CLOUD_PROVIDER.AWS, {}, False, False],
        [CLOUD_PROVIDER.GCP, {}, False, False],
        [CLOUD_PROVIDER.AZURE, {}, True, False],
        [CLOUD_PROVIDER.ALIBABA, {}, True, False],
        [CLOUD_PROVIDER.IBM, {}, True, False],
        [CLOUD_PROVIDER.TENCENT, {}, True, False],
        ["", {"some": "context"}, False, True],
        [CLOUD_PROVIDER.AWS, {"some": "context"}, False, True],
        [CLOUD_PROVIDER.GCP, {"some": "context"}, False, True],
    ],
)
def test_setup_once(
    cloud_provider, cloud_resource_context, warning_called, set_context_called
):
    from sentry_sdk.integrations.cloud_resource_context import (
        CloudResourceContextIntegration,
    )

    CloudResourceContextIntegration.cloud_provider = cloud_provider
    CloudResourceContextIntegration._get_cloud_resource_context = MagicMock(
        return_value=cloud_resource_context
    )

    with mock.patch(
        "sentry_sdk.integrations.cloud_resource_context.set_context"
    ) as fake_set_context:
        with mock.patch(
            "sentry_sdk.integrations.cloud_resource_context.logger.warning"
        ) as fake_warning:
            CloudResourceContextIntegration.setup_once()

            if set_context_called:
                fake_set_context.assert_called_once_with(
                    "cloud_resource", cloud_resource_context
                )
            else:
                fake_set_context.assert_not_called()

            if warning_called:
                assert fake_warning.call_count == 1
            else:
                fake_warning.assert_not_called()
