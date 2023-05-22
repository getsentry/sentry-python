import sentry_sdk

from sentry_sdk.integrations.modules import (
    ModulesIntegration,
    _get_installed_modules,
    _normalize_module_name,
)


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[ModulesIntegration()])
    events = capture_events()

    sentry_sdk.capture_exception(ValueError())

    (event,) = events
    assert "sentry-sdk" in event["modules"]
    assert "pytest" in event["modules"]


def test_installed_modules():
    try:
        from importlib import distributions, version

        importlib_available = True
    except ImportError:
        importlib_available = False

    try:
        import pkg_resources

        pkg_resources_available = True
    except ImportError:
        pkg_resources_available = False

    installed_modules = _get_installed_modules()

    # This one package is reported differently by importlib
    # and pkg_resources, but we don't really care, so let's
    # just ignore it
    installed_modules.pop("typing-extensions", None)
    installed_modules.pop("typing_extensions", None)

    if importlib_available:
        importlib_modules = {
            _normalize_module_name(dist.metadata["Name"]): version(
                dist.metadata["Name"]
            )
            for dist in distributions()
        }
        importlib_modules.pop("typing-extensions", None)
        assert installed_modules == importlib_modules

    if pkg_resources_available:
        pkg_resources_modules = {
            _normalize_module_name(dist.key): dist.version
            for dist in pkg_resources.working_set
        }
        pkg_resources_modules.pop("typing-extensions", None)
        assert installed_modules == pkg_resources_modules
