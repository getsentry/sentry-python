import boto3
import sentry_sdk 


monitor_slug = "python-sdk-aws-lambda-tests-cleanup"
monitor_config = {
    "schedule": {
        "type": "crontab",
        "value": "0 12 * * 0", # 12 o'clock on Sunday
    },
    "timezone": "UTC",
    "checkin_margin": 2,
    "max_runtime": 20,
    "failure_issue_threshold": 1,
    "recovery_threshold": 1,
}


@sentry_sdk.crons.monitor(monitor_slug=monitor_slug)
def delete_lambda_functions(prefix="test_"):
    """
    Delete all AWS Lambda functions in the current account
    where the function name matches the prefix
    """
    client = boto3.client("lambda", region_name="us-east-1")
    functions_deleted = 0
    
    functions_paginator = client.get_paginator("list_functions")
    for functions_page in functions_paginator.paginate():
        for func in functions_page["Functions"]:
            function_name = func["FunctionName"]
            if function_name.startswith(prefix):
                try:
                    response = client.delete_function(
                        FunctionName=func["FunctionArn"],
                    )
                    functions_deleted += 1
                except Exception as ex:
                    print(f"Got exception: {ex}")

    return functions_deleted
    

def lambda_handler(event, context):
    functions_deleted = delete_lambda_functions()
    
    sentry_sdk.metrics.gauge(
        key="num_aws_functions_deleted", 
        value=functions_deleted,
    )
    
    return {
        'statusCode': 200,
        'body': f"{functions_deleted} AWS Lambda functions deleted successfully."
    }
