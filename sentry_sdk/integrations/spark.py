from __future__ import absolute_import

from sentry_sdk.integrations import Integration

class SparkIntegration(Integration):
    identifier = "spark"

    @staticmethod
    def setup_once():
        # type: () -> None
        