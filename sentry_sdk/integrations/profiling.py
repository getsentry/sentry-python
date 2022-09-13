from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.profiler import _setup_profiler


class ProfilingIntegration(Integration):
    identifier = "profiling"

    @staticmethod
    def setup_once():
        # type: () -> None
        try:
            _setup_profiler()
        except ValueError:
            raise DidNotEnable("Profiling can only be enabled from the main thread.")
