#!/usr/bin/env bash

set -euo pipefail

FUNCTION_NAME=$1

echo "Getting ARN of newest Sentry lambda layer..."
LAYER_ARN=$(aws lambda list-layer-versions --layer-name SentryPythonServerlessSDKLocalDev --query "LayerVersions[0].LayerVersionArn" | tr -d '"')
echo "Done getting ARN of newest Sentry lambda layer $LAYER_ARN."

echo "Attaching Lamba layer to function $FUNCTION_NAME..."
aws lambda update-function-configuration --function-name "$FUNCTION_NAME"  --layers "$LAYER_ARN" --no-cli-pager
echo "Done attaching Lamba layer to function $FUNCTION_NAME."

echo "All done. Have a nice day!"
