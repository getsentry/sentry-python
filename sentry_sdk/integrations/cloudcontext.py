import json
import urllib3  # type: ignore

from sentry_sdk.integrations import Integration
from sentry_sdk.api import set_context

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Dict


CONTEXT_TYPE = "cloud"

AWS_METADATA_HOST = "169.254.169.254"
AWS_TOKEN_URL = "http://{}/latest/api/token".format(AWS_METADATA_HOST)
AWS_METADATA_URL = "http://{}/latest/dynamic/instance-identity/document".format(
    AWS_METADATA_HOST
)


class CLOUD_PROVIDER:
    ALIBABA = "alibaba_cloud"
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    IBM = "ibm_cloud"
    TENCENT = "tencent_cloud"


class CLOUD_PLATFORM:
    AWS_EC2 = "aws_ec2"


class CloudContextIntegration(Integration):
    """
    Adds cloud context to the Senty scope
    """

    identifier = "cloudcontext"

    aws_token = None
    http = urllib3.PoolManager()

    @classmethod
    def _is_aws(cls):
        # type: () -> bool
        try:
            r = cls.http.request(
                "PUT",
                AWS_TOKEN_URL,
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            )
            cls.aws_token = r.data
            return True
        except Exception:
            return False

    @classmethod
    def _get_aws_context(cls):
        # type: () -> Dict[str, str]
        ctx = {
            "cloud.provider": CLOUD_PROVIDER.AWS,
            "cloud.platform": CLOUD_PLATFORM.AWS_EC2,
        }

        try:
            r = cls.http.request(
                "GET",
                AWS_METADATA_URL,
                headers={"X-aws-ec2-metadata-token": cls.aws_token},
            )

            if r.status == 200:
                data = json.loads(r.data.decode("utf-8"))
                ctx.update(
                    {
                        "cloud.account.id": data["accountId"],
                        "cloud.availability_zone": data["availabilityZone"],
                        "cloud.region": data["region"],
                    }
                )
        except Exception:
            pass

        return ctx

    @classmethod
    def _get_cloud_provider(cls):
        # type: () -> str
        if cls._is_aws():
            return CLOUD_PROVIDER.AWS

        return ""

    @classmethod
    def _get_cloud_context(cls):
        # type: () -> Dict[str, str]
        getters = {
            CLOUD_PROVIDER.AWS: cls._get_aws_context,
        }

        cloud_provider = CloudContextIntegration._get_cloud_provider()
        if cloud_provider in getters.keys():
            return getters[cloud_provider]()

        return {}

    @staticmethod
    def setup_once():
        # type: () -> None
        context = CloudContextIntegration._get_cloud_context()
        if context != {}:
            set_context(CONTEXT_TYPE, context)
