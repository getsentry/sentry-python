IDENTIFIER = "aws_lambda"
ORIGIN = f"auto.function.{IDENTIFIER}"

CLOUD_PROVIDER = "aws"
CLOUD_PLATFORM = "aws_lambda"
SENTRY_KIND = "server"
LATEST_FUNCTION_VERSION = "$LATEST"

# buffer time time required to send timeout warning.
TIMEOUT_WARNING_BUFFER = 1500
MILLIS_TO_SECONDS = 1000.0

LAMBDA_METADATA_PATH = "/2026-01-15/metadata/execution-environment"
LAMBDA_METADATA_TIMEOUT = 0.5
