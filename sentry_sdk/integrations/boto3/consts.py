IDENTIFIER = "boto3"
ORIGIN = f"auto.http.{IDENTIFIER}"
# origin of `gen_ai` spans, e.g. for Bedrock Runtime calls.
AI_ORIGIN = f"auto.ai.{IDENTIFIER}"

# value is used by `rpc.system` (deprecated in OTel, but we still support it for now) and `rpc.system.name`
# https://opentelemetry.io/docs/specs/semconv/cloud-providers/aws-sdk/#aws-sdk-spans
AWS_RPC_SYSTEM_NAME = "aws-api"

# default ports for HTTP and HTTPS
DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
}
