# Test AWS Lambda functions locally

An easy way to run an AWS Lambda function with the Sentry SDK locally.

This is a small helper to create a AWS Lambda function that includes the
currently checked out Sentry SDK and runs it in a local AWS Lambda environment.

Currently only embedding the Sentry SDK into the Lambda function package
is supported. Adding the SDK as Lambda Layer is not possible at the moment.

## Prerequisites

- Set `SENTRY_DSN` environment variable. The Lambda function will use this DSN.
- You need to have Docker installed and running.

## Run Lambda function

- Update `lambda_function.py` to include your test code.
- Run `./deploy-lambda-locally.sh`. This will:
    - Install [AWS SAM](https://aws.amazon.com/serverless/sam/) in a virtual Python environment
    - Create a lambda function package in `package/` that includes
        - The currently checked out Sentry SDK
        - All dependencies of the Sentry SDK (certifi and urllib3)
        - The actual function defined in `lamdba_function.py`.
    - Zip everything together into lambda_deployment_package.zip
    - Run a local Lambda environment that serves that Lambda function.
- Point your browser to `http://127.0.0.1:3000` to access your Lambda function.
    - Currently GET and POST requests are possible. This is defined in `template.yaml`.
