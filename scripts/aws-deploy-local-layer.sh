#!/usr/bin/env bash
#
# Builds and deploys the Sentry AWS Lambda layer (including the Sentry SDK and the Sentry Lambda Extension)
#
# The currently checked out version of the SDK in your local directory is used.
# The latest version of the Lambda Extension is fetched from the Sentry Release Registry.
#

set -euo pipefail

# Creating Lambda layer
echo "Creating Lambda layer in ./dist-serverless ..."
make aws-lambda-layer
echo "Done creating Lambda layer in ./dist-serverless."

# IMPORTANT:
# Please make sure that this does the same as the GitHub action that
# is building the Lambda layer in production!
# see: https://github.com/getsentry/action-build-aws-lambda-extension/blob/main/action.yml#L23-L40

# Adding Sentry Lambda extension to Lambda layer
echo "Adding Sentry Lambda extension to Lambda layer in ./dist-serverless ..."
mkdir -p dist-serverless/extensions
curl -0 --silent --output dist-serverless/extensions/sentry-lambda-extension `curl -s https://release-registry.services.sentry.io/apps/sentry-lambda-extension/latest | jq -r .files.\"sentry-lambda-extension\".url`
chmod +x dist-serverless/extensions/sentry-lambda-extension
echo "Done adding Sentry Lambda extension to Lambda layer in ./dist-serverless."

echo "Setting configuration for extension in in ./dist-serverless/extensions ..."
mkdir -p dist-serverless/extensions/.relay/
cat << EOT >> dist-serverless/extensions/.relay/config.yml
---
relay:
	mode: proxy
	upstream: "https://sentry.io"
	host: 127.0.0.1
	port: 3000
limits:
    shutdown_timeout: 2
EOT
echo "Done setting configuration for extension in in ./dist-serverless/extensions ..."


# Zip Lambda layer and included Lambda extension
echo "Zipping Lambda layer and included Lambda extension..."
cd dist-serverless/
zip -r ../sentry-python-serverless-x.x.x-dev.zip \
    . \
    --exclude \*__pycache__\* --exclude \*.yml
cd ..
echo "Done Zipping Lambda layer and included Lambda extension to ./sentry-python-serverless-x.x.x-dev.zip."


# Deploying zipped Lambda layer to AWS
echo "Deploying zipped Lambda layer to AWS..."

aws lambda publish-layer-version \
    --layer-name "SentryPythonServerlessSDK-local-dev" \
    --region "eu-central-1" \
    --zip-file "fileb://sentry-python-serverless-x.x.x-dev.zip" \
    --description "Local test build of SentryPythonServerlessSDK (can be deleted)" \
    --no-cli-pager

echo "Done deploying zipped Lambda layer to AWS as 'SentryPythonServerlessSDK-local-dev'."

echo "All done. Have a nice day!"
