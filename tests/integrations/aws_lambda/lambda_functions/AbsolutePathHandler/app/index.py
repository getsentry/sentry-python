# counts the invocations since this module was loaded; gets reset on cold start or
# when the module is reloaded.
num_invocations = 0


def handler(event, context):
    global num_invocations
    num_invocations += 1
    return {"num_invocations": num_invocations}
