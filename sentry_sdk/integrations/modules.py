from __future__ import absolute_import

from sentry_sdk.api import configure_scope
from sentry_sdk.integrations import Integration

_installed_modules = None


def _generate_installed_modules():
    try:
        import pkg_resources
    except ImportError:
        return

    for info in pkg_resources.working_set:
        yield info.key, info.version


def _get_installed_modules():
    global _installed_modules
    if _installed_modules is None:
        _installed_modules = dict(_generate_installed_modules())
    return _installed_modules


class ModulesIntegration(Integration):
    identifier = "modules"

    def install(self):
        with configure_scope() as scope:

            @scope.add_event_processor
            def processor(event, hint):
                if "modules" not in event:
                    event["modules"] = dict(_get_installed_modules())
                return event
