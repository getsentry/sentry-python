# counts the invocations since this module was loaded.
invocations = 0


def handler(event, context):
    global invocations
    invocations += 1
    return {"invocations": invocations}
