from somelayer import layer_function


def handler(event, context):
    message = f"Hello, {event['name']}! ({layer_function()})"
    return {"message": message}
