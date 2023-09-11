import gql
from sentry_sdk.integrations import Integration
from typing import Any


class GQLIntegration(Integration):
    @staticmethod
    def setup_once() -> None:
        _patch_execute()


def _patch_execute() -> None:
    real_execute = gql.Client.execute

    def patch_execute(self: gql.Client, *args: Any, **kwargs: Any) -> Any:
        print("I have been monkeypatched 🐵")
        return real_execute(self, *args, **kwargs)

    gql.Client.execute = patch_execute
