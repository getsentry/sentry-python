#!/usr/bin/env bash
#
# Attach the layer first with: ./scripts/aws/aws-attach-layer-to-lambda-function.sh
#
# Replaces handler, SENTRY_DSN, SENTRY_INITIAL_HANDLER and SENTRY_TRACES_SAMPLE_RATE.

# Usage: ./scripts/aws/aws-configure-layer-on-lambda-function.sh <lambda-function-name> <dsn> <region (optional)>

set -euo pipefail

if [ $# -lt 2 ] || [ $# -gt 3 ]; then
	SCRIPT_NAME=$(basename "$0")
	echo "ERROR: Missing arguments."
	echo "Usage: $SCRIPT_NAME <lambda-function-name> <dsn> <region (optional)>"
	exit 1
fi

FUNCTION_NAME=$1
DSN=$2
SENTRY_HANDLER="sentry_sdk.integrations.init_serverless_sdk.sentry_lambda_handler"
REGION=${3:-"eu-north-1"}
TRACES_SAMPLE_RATE="1.0"

echo "Fetching current configuration for function '$FUNCTION_NAME'..."
CONFIG=$(aws lambda get-function-configuration \
	--function-name "$FUNCTION_NAME" \
	--region "$REGION" \
	--no-cli-pager)
CURRENT_HANDLER=$(echo "$CONFIG" | jq -r '.Handler')

if [ "$CURRENT_HANDLER" = "$SENTRY_HANDLER" ]; then
	INITIAL_HANDLER=$(echo "$CONFIG" | jq -r '.Environment.Variables.SENTRY_INITIAL_HANDLER // empty')
	if [ -z "$INITIAL_HANDLER" ] || [ "$INITIAL_HANDLER" = "$SENTRY_HANDLER" ]; then
		echo "Handler is already set to '$SENTRY_HANDLER' but SENTRY_INITIAL_HANDLER is missing or points at it."
		echo "Set SENTRY_INITIAL_HANDLER to real handler first."
		exit 1
	fi
	echo "SENTRY_INITIAL_HANDLER already set to $INITIAL_HANDLER."
else
	INITIAL_HANDLER=$CURRENT_HANDLER
fi

ENV_JSON=$(echo "$CONFIG" | jq -c \
	--arg dsn "$DSN" \
	--arg initial "$INITIAL_HANDLER" \
	--arg traces_sample_rate "$TRACES_SAMPLE_RATE" \
	'
    ((.Environment.Variables // {})
     | del(.SENTRY_DSN, .SENTRY_INITIAL_HANDLER, .SENTRY_TRACES_SAMPLE_RATE))
    + {
        SENTRY_DSN: $dsn,
        SENTRY_INITIAL_HANDLER: $initial,
        SENTRY_TRACES_SAMPLE_RATE: $traces_sample_rate
    }
    | {Variables: .}
    ')

echo "Updating function configuration..."
aws lambda update-function-configuration \
	--function-name "$FUNCTION_NAME" \
	--region "$REGION" \
	--handler "$SENTRY_HANDLER" \
	--environment "$ENV_JSON" \
	--no-cli-pager
