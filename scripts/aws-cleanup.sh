#!/bin/sh
# Delete all AWS Lambda functions
for func in $(aws lambda list-functions | jq .Functions[].FunctionName); do
    echo "Deleting $func"
    aws lambda delete-function --function-name $func
done
