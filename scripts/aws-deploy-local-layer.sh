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
# Please make sure that this part does the same as the GitHub action that
# is building the Lambda layer in production!
# see: https://github.com/getsentry/action-build-aws-lambda-extension/blob/main/action.yml#L23-L40

echo "Downloading relay..."
mkdir -p dist-serverless/relay
# curl -0 --silent \
#     --output dist-serverless/relay/relay \
#     "$(curl -s https://release-registry.services.sentry.io/apps/relay/latest | jq -r .files.\"relay-Linux-x86_64\".url)"
cp /Users/antonpirker/code/relay/target/x86_64-unknown-linux-gnu/release/relay dist-serverless/relay/ # REMOVE THIS !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
chmod +x dist-serverless/relay/relay
echo "Done downloading relay."

echo "Creating start script..."
mkdir -p dist-serverless/extensions
cat > dist-serverless/extensions/sentry-lambda-extension << EOT
#!/bin/bash
set -euo pipefail
exec /opt/relay/relay run \
    --mode=proxy \
    --shutdown-timeout=2 \
    --upstream-dsn="\$SENTRY_DSN" \
    --aws-runtime-api="\$AWS_LAMBDA_RUNTIME_API"
EOT
chmod +x dist-serverless/extensions/sentry-lambda-extension
echo "Done creating start script."

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
