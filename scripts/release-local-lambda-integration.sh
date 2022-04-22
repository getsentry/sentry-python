#!/usr/bin/env bash

set -euo pipefail


# Creating Lambda layer
echo "Creating Lambda layer in ./dist-serverless ..."
make aws-lambda-layer
echo "Done creating Lambda layer in ./dist-serverless."


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
EOT
echo "Done setting configuration for extension in in ./dist-serverless/extensions ..."


# Zip Lambda layer and included Lambda extension
echo "Zipping Lambda layer and included Lambda extension..."
zip -r sentry-python-serverless-x.x.x-dev.zip dist-serverless/ -x \*__pycache__\* -x \*.yml
echo "Done Zipping Lambda layer and included Lambda extension to ./sentry-python-serverless-x.x.x-dev.zip."


# Deploying zipped Lambda layer to AWS
echo "Deploying zipped Lambda layer to AWS..."

aws lambda publish-layer-version \
    --layer-name "SentryPythonServerlessSDKLocalDev" \
    --region "eu-central-1" \
    --zip-file "fileb://sentry-python-serverless-x.x.x-dev.zip" \
    --description "Local test build of SentryPythonServerlessSDK (can be deleted)"

echo "Done deploying zipped Lambda layer to AWS as SentryPythonServerlessSDKLocalDev."

echo "All done. Have a nice day!"
