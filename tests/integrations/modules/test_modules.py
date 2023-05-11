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
    if importlib_available:
        assert installed_modules == {
            _normalize_module_name(dist.metadata["Name"]): version(
                dist.metadata["Name"]
            )
            for dist in distributions()
        }
    if pkg_resources_available:
        assert installed_modules == {
            _normalize_module_name(dist.key): dist.version
            for dist in pkg_resources.working_set
        }
