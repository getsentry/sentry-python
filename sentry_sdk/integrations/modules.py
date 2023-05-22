from __future__ import absolute_import

from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import Tuple
    from typing import Iterator

    from sentry_sdk._types import Event


_installed_modules = None


def _normalize_module_name(name):
    # type: (str) -> str
    return name.lower()


def _generate_installed_modules():
    # type: () -> Iterator[Tuple[str, str]]
    try:
        from importlib.metadata import distributions, version

        for dist in distributions():
            yield _normalize_module_name(dist.metadata["Name"]), version(
                dist.metadata["Name"]
            )

    except ImportError:
        # < py3.8
        try:
            import pkg_resources
        except ImportError:
            return

        for info in pkg_resources.working_set:
            yield _normalize_module_name(info.key), info.version


def _get_installed_modules():
    # type: () -> Dict[str, str]
    global _installed_modules
    if _installed_modules is None:
        _installed_modules = dict(_generate_installed_modules())
    return _installed_modules


class ModulesIntegration(Integration):
    identifier = "modules"

    @staticmethod
    def setup_once():
        # type: () -> None
        @add_global_event_processor
        def processor(event, hint):
            # type: (Event, Any) -> Dict[str, Any]
            if event.get("type") == "transaction":
                return event

            if Hub.current.get_integration(ModulesIntegration) is None:
                return event

            event["modules"] = _get_installed_modules()
            return event
