def handler(event, context):
    return {"message": f"Hello, {event['name']}!"}
