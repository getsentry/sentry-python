#!/usr/bin/env bash
#
# Attaches the layer `SentryPythonServerlessSDK-local-dev` to a given lambda function.
#

set -euo pipefail

# Check for argument
if [ $# -eq 0 ]
  then
    SCRIPT_NAME=$(basename "$0")
    echo "ERROR: No argument supplied. Please give the name of a Lambda function!"
    echo ""
    echo "Usage: $SCRIPT_NAME <lambda-function-name>"
    echo ""
    exit 1
fi

FUNCTION_NAME=$1

echo "Getting ARN of newest Sentry lambda layer..."
LAYER_ARN=$(aws lambda list-layer-versions --layer-name SentryPythonServerlessSDK-local-dev --query "LayerVersions[0].LayerVersionArn" | tr -d '"')
echo "Done getting ARN of newest Sentry lambda layer $LAYER_ARN."

echo "Attaching Lamba layer to function $FUNCTION_NAME..."
echo "Warning: This remove all other layers!"
aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --layers "$LAYER_ARN" \
    --no-cli-pager
echo "Done attaching Lamba layer to function '$FUNCTION_NAME'."

echo "All done. Have a nice day!"
