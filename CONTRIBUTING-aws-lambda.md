# Contributing to Sentry AWS Lambda Layer

All the general terms of the [CONTRIBUTING.md](CONTRIBUTING.md) apply.

## Development environment

You need to have a AWS account and AWS CLI installed and setup.

We put together two helper functions that can help you with development:

- `./scripts/aws-deploy-local-layer.sh`

  This script [scripts/aws-deploy-local-layer.sh](scripts/aws-deploy-local-layer.sh) will take the code you have checked out locally, create a Lambda layer out of it and deploy it to the `eu-central-1` region of your configured AWS account using `aws` CLI.

  The Lambda layer will have the name `SentryPythonServerlessSDK-local-dev`

- `./scripts/aws-attach-layer-to-lambda-function.sh`

  You can use this script [scripts/aws-attach-layer-to-lambda-function.sh](scripts/aws-attach-layer-to-lambda-function.sh) to attach the Lambda layer you just deployed (using the first script) onto one of your existing Lambda functions. You will have to give the name of the Lambda function to attach onto as an argument. (See the script for details.)

With this two helper scripts it should be easy to rapidly iterate your development on the Lambda layer.
