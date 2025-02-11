from functools import wraps
from typing import Any, TYPE_CHECKING

from sentry_sdk.feature_flags import add_feature_flag
from sentry_sdk.integrations import Integration, DidNotEnable

try:
    from statsig import statsig as statsig_module
except ImportError:
    raise DidNotEnable("statsig is not installed")

if TYPE_CHECKING:
    from statsig.statsig_user import StatsigUser


class StatsigIntegration(Integration):
    identifier = "statsig"

    @staticmethod
    def setup_once():
        # type: () -> None
        # Wrap and patch evaluation method(s) in the statsig module
        old_check_gate = statsig_module.check_gate

        @wraps(old_check_gate)
        def sentry_check_gate(user, gate, *args, **kwargs):
            # type: (StatsigUser, str, *Any, **Any) -> Any
            enabled = old_check_gate(user, gate, *args, **kwargs)
            add_feature_flag(gate, enabled)
            return enabled

        statsig_module.check_gate = sentry_check_gate
