from functools import wraps
from typing import Any, TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import Integration, DidNotEnable

import importlib

try:
    # The statsig package has the same name as this file. We use importlib to avoid conflicts.
    statsig = importlib.import_module("statsig.statsig")
except ImportError:
    raise DidNotEnable("statsig is not installed")

if TYPE_CHECKING:
    statsig_user = importlib.import_module("statsig.statsig_user")
    StatsigUser = statsig_user.StatsigUser


class StatsigIntegration(Integration):
    identifier = "statsig"

    @staticmethod
    def setup_once():
        # type: () -> None
        # Wrap and patch evaluation method(s) in the statsig module
        old_check_gate = statsig.check_gate

        @wraps(old_check_gate)
        def sentry_check_gate(user, gate, *args, **kwargs):
            # type: (StatsigUser, str, *Any, **Any) -> Any
            enabled = old_check_gate(user, gate, *args, **kwargs)

            flags = sentry_sdk.get_current_scope().flags
            flags.set(gate, enabled)

            return enabled

        statsig.check_gate = sentry_check_gate
