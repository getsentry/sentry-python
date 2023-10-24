#!/bin/sh
#
# Helper script to clean up AWS Lambda functions created
# by the test suite (tests/integrations/aws_lambda/test_aws.py).
#
# This will delete all Lambda functions named `test_function_*`.
#

export AWS_DEFAULT_REGION="us-east-1"
export AWS_ACCESS_KEY_ID="$SENTRY_PYTHON_TEST_AWS_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$SENTRY_PYTHON_TEST_AWS_SECRET_ACCESS_KEY"

for func in $(aws lambda list-functions --output text --query 'Functions[?starts_with(FunctionName, `test_`) == `true`].FunctionName'); do
    echo "Deleting $func"
    aws lambda delete-function --function-name "$func"
done

echo "All done! Have a nice day!"
