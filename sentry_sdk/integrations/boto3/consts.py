IDENTIFIER = "boto3"
ORIGIN = f"auto.http.{IDENTIFIER}"
CLOUD_PROVIDER = "aws"

# value is used by `rpc.system` (deprecated in OTel, but we still support it for now) and `rpc.system.name`
# https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
AWS_RPC_SYSTEM_NAME = "aws-api"

# default ports for HTTP and HTTPS
DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
}
