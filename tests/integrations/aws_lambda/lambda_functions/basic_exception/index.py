
def handler(event, context):
    1/0
    raise Exception("Oh!")

    return {
        "event": event,
    }
