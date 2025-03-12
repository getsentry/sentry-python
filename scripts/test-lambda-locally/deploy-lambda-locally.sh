#!/usr/bin/env bash

# exit on first error
set -xeuo pipefail

# Setup local AWS Lambda environment

# Install uv if it's not installed
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

uv sync

# Create a deployment package of the lambda function in `lambda_function.py`.
rm -rf package && mkdir -p package              
pip install ../../../sentry-python -t package/ --upgrade
cp lambda_function.py package/ 
cd package && zip -r ../lambda_deployment_package.zip . && cd ..

# Start the local Lambda server with the new function (defined in template.yaml)
uv run sam local start-api \
    --skip-pull-image \
    --force-image-build \
    --parameter-overrides SentryDsn=$SENTRY_DSN
