import re
from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._utils import (
    _extract_attributes,
    _get_aws_arn_attributes,
)
from sentry_sdk.utils import capture_internal_exceptions

if TYPE_CHECKING:
    from typing import Any

    from sentry_sdk._types import Attributes
    from sentry_sdk.integrations.boto3._context import AwsCallContext

# secret-arn ends with name, a hyphen and six random chars (`…:secret:db-password-AbCdEf`).
# https://docs.aws.amazon.com/secretsmanager/latest/userguide/troubleshoot.html#ARN_secretnamehyphen
_ARN_SUFFIX = re.compile(r"-[A-Za-z0-9]{6}$")


def _get_name_from_arn(arn: str) -> str:
    # names can't contain ":"; a partial arn has no suffix (`…:secret:db-backup` → `db`).
    # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_CreateSecret.html#SecretsManager-CreateSecret-request-Name
    return _ARN_SUFFIX.sub("", arn.rsplit(":", 1)[1])


class _SecretsManagerExtension(_ServiceExtension):
    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        attributes: "Attributes" = {}
        with capture_internal_exceptions():
            # only `CreateSecret` takes a `Name`; every other operations acts on
            # a secret by name or full/partial arn.
            if "Name" in ctx.params:
                attributes[SPANDATA.AWS_SECRETSMANAGER_SECRET_NAME] = ctx.params["Name"]

            if "SecretId" in ctx.params:
                secret_id = ctx.params["SecretId"]
                arn_attributes = _get_aws_arn_attributes(
                    (secret_id,), ("secretsmanager",)
                )
                attributes.update(arn_attributes)
                if arn_attributes:
                    attributes[SPANDATA.AWS_SECRETSMANAGER_SECRET_ARN] = secret_id
                    attributes[SPANDATA.AWS_SECRETSMANAGER_SECRET_NAME] = (
                        _get_name_from_arn(secret_id)
                    )
                else:
                    attributes[SPANDATA.AWS_SECRETSMANAGER_SECRET_NAME] = secret_id
        return attributes

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        # when returned, the full `ARN` and exact `Name` replace the `SecretId` values.
        # failed calls, tagging and `ValidateResourcePolicy` return neither, the
        # replication apis no `Name`; there the request's values remain.
        return _extract_attributes(
            response,
            (
                ("ARN", SPANDATA.AWS_SECRETSMANAGER_SECRET_ARN),
                ("Name", SPANDATA.AWS_SECRETSMANAGER_SECRET_NAME),
            ),
        )
