import json
import urllib3  # type: ignore

from sentry_sdk.integrations import Integration
from sentry_sdk.api import set_context
from sentry_sdk.utils import logger

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Dict


CONTEXT_TYPE = "cloud"

AWS_METADATA_HOST = "169.254.169.254"
AWS_TOKEN_URL = "http://{}/latest/api/token".format(AWS_METADATA_HOST)
AWS_METADATA_URL = "http://{}/latest/dynamic/instance-identity/document".format(
    AWS_METADATA_HOST
)

GCP_METADATA_HOST = "metadata.google.internal"
GCP_METADATA_URL = "http://{}/computeMetadata/v1/?recursive=true".format(
    GCP_METADATA_HOST
)


class CLOUD_PROVIDER:
    """
    Name of the cloud provider.
    see https://opentelemetry.io/docs/reference/specification/resource/semantic_conventions/cloud/
    """

    ALIBABA = "alibaba_cloud"
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    IBM = "ibm_cloud"
    TENCENT = "tencent_cloud"


class CLOUD_PLATFORM:
    """
    The cloud platform.
    see https://opentelemetry.io/docs/reference/specification/resource/semantic_conventions/cloud/
    """

    AWS_EC2 = "aws_ec2"
    GCP_COMPUTE_ENGINE = "gcp_compute_engine"


class CloudContextIntegration(Integration):
    """
    Adds cloud context to the Senty scope
    """

    identifier = "cloudcontext"

    cloud_provider = ""

    aws_token = ""
    http = urllib3.PoolManager()

    gcp_metadata = {}

    def __init__(self, cloud_provider=""):
        # type: (str) -> None
        CloudContextIntegration.cloud_provider = cloud_provider

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
    def _is_gcp(cls):
        # type: () -> bool
        try:
            r = cls.http.request(
                "GET",
                GCP_METADATA_URL,
                headers={"Metadata-Flavor": "Google"},
            )
            cls.gcp_metadata = json.loads(r.data.decode("utf-8"))
            return True

        except Exception:
            return False

    @classmethod
    def _get_gcp_context(cls):
        # type: () -> Dict[str, str]
        ctx = {
            "cloud.provider": CLOUD_PROVIDER.GCP,
            "cloud.platform": CLOUD_PLATFORM.GCP_COMPUTE_ENGINE,
        }

        try:
            ctx["cloud.account.id"] = cls.gcp_metadata["project"]["projectId"]
        except Exception:
            pass

        try:
            ctx["cloud.zone"] = cls.gcp_metadata["instance"]["zone"].split("/")[-1]
        except Exception:
            pass

        try:
            ctx["cloud.region"] = cls.gcp_metadata["instance"]["region"].split("/")[-1]
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
        cloud_provider = (
            cls.cloud_provider
            if cls.cloud_provider != ""
            else CloudContextIntegration._get_cloud_provider()
        )
        if cloud_provider in context_getters.keys():
            return context_getters[cloud_provider]()

        return {}

    @staticmethod
    def setup_once():
        # type: () -> None
        cloud_provider = CloudContextIntegration.cloud_provider
        unsupported_cloud_provider = (
            cloud_provider != "" and cloud_provider not in context_getters.keys()
        )

        if unsupported_cloud_provider:
            logger.warning(
                "Invalid value for cloud_provider: %s (must be in %s). Falling back to autodetection...",
                CloudContextIntegration.cloud_provider,
                list(context_getters.keys()),
            )

        context = CloudContextIntegration._get_cloud_context()
        if context != {}:
            set_context(CONTEXT_TYPE, context)


context_getters = {
    CLOUD_PROVIDER.AWS: CloudContextIntegration._get_aws_context,
    CLOUD_PROVIDER.GCP: CloudContextIntegration._get_gcp_context,
}
