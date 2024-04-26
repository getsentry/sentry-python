sentryPythonDeleteTestFunctions
===============================

This AWS Lambda function deletes all AWS Lambda functions in the current AWS account that are prefixed with `test_`.
The functions that are deleted are created by the Google Actions CI checks running on every PR of the `sentry-python` repository.

The Lambda function has been deployed here:
- AWS Account ID: `943013980633`
- Region: `us-east-1`
- Function ARN: `arn:aws:lambda:us-east-1:943013980633:function:sentryPythonDeleteTestFunctions`

This function also emits Sentry Metrics and Sentry Crons checkins to the `sentry-python` project in the `Sentry SDKs` organisation on Sentry.io:
https://sentry-sdks.sentry.io/projects/sentry-python/?project=5461230