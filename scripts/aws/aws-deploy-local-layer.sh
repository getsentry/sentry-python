#!/usr/bin/env bash
#
# Builds and deploys the `SentryPythonServerlessSDK-local-dev` AWS Lambda layer (containing the Sentry SDK)
#
# The currently checked out version of the SDK in your local directory is used.
#

set -euo pipefail

# Creating Lambda layer
echo "Creating Lambda layer in ./dist ..."
make aws-lambda-layer
echo "Done creating Lambda layer in ./dist"

# Deploying zipped Lambda layer to AWS
ZIP=$(ls dist | grep serverless | head -n 1)
echo "Deploying zipped Lambda layer $ZIP to AWS..."

aws lambda publish-layer-version \
    --layer-name "SentryPythonServerlessSDK-local-dev" \
    --region "eu-central-1" \
    --zip-file "fileb://dist/$ZIP" \
    --description "Local test build of SentryPythonServerlessSDK (can be deleted)" \
    --compatible-runtimes python3.7 python3.8 python3.9 python3.10 python3.11 \
    --no-cli-pager

echo "Done deploying zipped Lambda layer to AWS as 'SentryPythonServerlessSDK-local-dev'."

echo "All done. Have a nice day!"
