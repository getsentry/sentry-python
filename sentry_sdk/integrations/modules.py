from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.utils import _get_installed_modules

if TYPE_CHECKING:
    from typing import Any, Dict, Optional

    from sentry_sdk._types import Event


class _InstalledModules(dict):  # type: ignore[type-arg]
    def __sentry_repr__(self) -> "Dict[str, str]":
        return dict(self)


_installed_modules: "Optional[_InstalledModules]" = None


def _get_installed_modules_for_event() -> _InstalledModules:
    global _installed_modules

    if _installed_modules is None:
        _installed_modules = _InstalledModules(_get_installed_modules())

    return _installed_modules


class ModulesIntegration(Integration):
    identifier = "modules"

    @staticmethod
    def setup_once() -> None:
        @add_global_event_processor
        def processor(event: "Event", hint: "Any") -> "Event":
            if event.get("type") == "transaction":
                return event

            if sentry_sdk.get_client().get_integration(ModulesIntegration) is None:
                return event

            event["modules"] = _get_installed_modules_for_event()
            return event
