from typing import TYPE_CHECKING

from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3._services.dynamodb import _DynamoDBExtension
from sentry_sdk.integrations.boto3._services.kinesis import _KinesisExtension
from sentry_sdk.integrations.boto3._services.lambda_ import _LambdaExtension
from sentry_sdk.integrations.boto3._services.s3 import _S3Extension
from sentry_sdk.integrations.boto3._services.secretsmanager import (
    _SecretsManagerExtension,
)
from sentry_sdk.integrations.boto3._services.sns import _SNSExtension
from sentry_sdk.integrations.boto3._services.sqs import _SQSExtension
from sentry_sdk.integrations.boto3._services.stepfunctions import (
    _StepFunctionsExtension,
)

if TYPE_CHECKING:
    from typing import Dict, Optional


# add a ServiceExtension here when one is implemented. for example:
#   _SERVICE_EXTENSIONS = {"s3": _S3Extension()}
# when py 3.15 drops, we might want to take a look at using
# a lazy-loading approach using the new `lazy` keyword.
_SERVICE_EXTENSIONS: "Dict[Optional[str], _ServiceExtension]" = {
    "dynamodb": _DynamoDBExtension(),
    "kinesis": _KinesisExtension(),
    "lambda": _LambdaExtension(),
    "s3": _S3Extension(),
    "secretsmanager": _SecretsManagerExtension(),
    "sns": _SNSExtension(),
    "sqs": _SQSExtension(),
    "stepfunctions": _StepFunctionsExtension(),
}


def _resolve_service(
    service_name: "Optional[str]",
) -> "_ServiceExtension":
    """Return the extension for a service, or `_ServiceExtension()` for generic instrumentation."""
    return _SERVICE_EXTENSIONS.get(service_name, _ServiceExtension())
