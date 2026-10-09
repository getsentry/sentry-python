from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.aws_lambda.consts import IDENTIFIER, ORIGIN


class AwsLambdaIntegration(Integration):
    identifier = IDENTIFIER
    origin = ORIGIN

    def __init__(self, timeout_warning: bool = False) -> None:
        self.timeout_warning = timeout_warning

    @staticmethod
    def setup_once() -> None:
        from sentry_sdk.integrations.aws_lambda._runtime import _setup_once

        _setup_once()
