from sentry_sdk.integrations.starlette import StarletteIntegration


class FastAPIIntegration(StarletteIntegration):
    identifier = "fastapi"
