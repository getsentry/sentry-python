import time


def handler(event, context):
    time.sleep(15)
    return {
        "event": event,
    }
