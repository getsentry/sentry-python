def handler(event, context):
    return {"statusCode": int(event["queryStringParameters"]["status"])}
