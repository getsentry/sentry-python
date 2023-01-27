from __future__ import absolute_import

from sentry_sdk.integrations import DidNotEnable, Integration

try:
    from arq.version import VERSION as ARQ_VERSION
except ImportError:
    raise DidNotEnable("Arq is not installed")


class ArqIntegration(Integration):
    identifier = "arq"

    @staticmethod
    def setup_once():
        # type: () -> None

        try:
            if isinstance(ARQ_VERSION, str):
                version = tuple(map(int, ARQ_VERSION.split(".")[:2]))
            else:
                version = ARQ_VERSION.version[:2]
        except (TypeError, ValueError):
            raise DidNotEnable("arq version unparsable: {}".format(ARQ_VERSION))

        if version < (0, 23):
            raise DidNotEnable("arq 0.23 or newer required.")
