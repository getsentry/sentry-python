import sentry_sdk
from sentry_sdk.integrations.excepthook import ExcepthookIntegration
from sentry_sdk.integrations.atexit import AtexitIntegration
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.stdlib import StdlibIntegration


sentry_sdk.Client(
    dsn="https://<key>@sentry.io/<project>",
    default_integrations=False,
    integrations=[
        ExcepthookIntegration(),
        AtexitIntegration(),
        DedupeIntegration(),
        StdlibIntegration(),
    ],
    environment="Production",
    release="1.0.0",
    send_default_pii=False,
    max_breadcrumbs=5,
)

with sentry_sdk.push_scope() as scope:
    scope.user = {"email": "john.doe@example.com"}
    scope.set_tag("page_locale", "de-at")
    scope.set_extra("request", {"id": "d5cf8a0fd85c494b9c6453c4fba8ab17"})
    scope.level = "warning"
    sentry_sdk.capture_message("Something went wrong!")

sentry_sdk.add_breadcrumb(category="auth", message="Authenticated user", level="info")

try:
    1 / 0
except Exception as e:
    sentry_sdk.capture_exception(e)
