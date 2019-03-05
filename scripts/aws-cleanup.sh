#!/bin/sh
# Delete all AWS Lambda functions

export AWS_ACCESS_KEY_ID="$SENTRY_PYTHON_TEST_AWS_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$SENTRY_PYTHON_TEST_AWS_SECRET_ACCESS_KEY"
export AWS_IAM_ROLE="$SENTRY_PYTHON_TEST_AWS_IAM_ROLE"

for func in $(aws lambda list-functions | jq -r .Functions[].FunctionName); do
    echo "Deleting $func"
    aws lambda delete-function --function-name $func
done
