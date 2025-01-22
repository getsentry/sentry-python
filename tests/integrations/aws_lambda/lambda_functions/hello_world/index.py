import sentry_sdk


def handler(event, context):
    message = f"Hello, {event['name']}!"
    sentry_sdk.capture_message(f"[SENTRY MESSAGE] {message}")
    return {"message": message}
