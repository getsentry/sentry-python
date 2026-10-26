IDENTIFIER = "boto3"
ORIGIN = f"auto.aws.{IDENTIFIER}"
# aiobotocore clients are botocore clients, instrumented by the same code.
AIOBOTOCORE_IDENTIFIER = "aiobotocore"
AIOBOTOCORE_ORIGIN = f"auto.aws.{AIOBOTOCORE_IDENTIFIER}"
CLOUD_PROVIDER = "aws"

# value is used by `rpc.system` (deprecated in OTel, but we still support it for now) and `rpc.system.name`
# https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
AWS_RPC_SYSTEM_NAME = "aws-api"

# default ports for HTTP and HTTPS
DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
}
