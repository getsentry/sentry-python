import sys
import ast
import types
import pkgutil
import importlib
import pathlib
import pytest

from sentry_sdk import integrations
from sentry_sdk.integrations import _DEFAULT_INTEGRATIONS, Integration


def pytest_generate_tests(metafunc):
    """
    All submodules of sentry_sdk.integrations are picked up, so modules
    without a subclass of sentry_sdk.integrations.Integration are also tested
    for poorly gated imports.

    This approach was chosen to keep the implementation simple.
    """
    if "integration_submodule_name" in metafunc.fixturenames:
        submodule_names = {
            submodule_name
            for _, submodule_name, _ in pkgutil.walk_packages(integrations.__path__)
        } - {"beam", "spark", "unraisablehook"}

        metafunc.parametrize(
            "integration_submodule_name",
            submodule_names,
        )


def find_unrecognized_dependencies(tree):
    """
    Finds unrecognized imports in the AST for a Python module. In an empty
    environment the set of non-standard library modules is returned.
    """
    unrecognized_dependencies = set()
    package_name = lambda name: name.split(".")[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = package_name(alias.name)

                try:
                    if not importlib.util.find_spec(root):
                        unrecognized_dependencies.add(root)
                except ValueError:
                    continue

        elif isinstance(node, ast.ImportFrom):
            # if node.level is not 0 the import is relative
            if node.level > 0 or node.module is None:
                continue

            root = package_name(node.module)

            try:
                if not importlib.util.find_spec(root):
                    unrecognized_dependencies.add(root)
            except ValueError:
                continue

    return unrecognized_dependencies


@pytest.mark.skipif(
    sys.version_info < (3, 7), reason="asyncpg imports __future__.annotations"
)
def test_shadowed_modules_when_importing_integrations(
    sentry_init, integration_submodule_name
):
    """
    Check that importing integrations for third-party module raises an
    DidNotEnable exception when the associated module is shadowed by an empty
    module.

    An integration is determined to be for a third-party module if it cannot
    be imported in the environment in which the tests run.
    """
    parts = integration_submodule_name.split(".")
    module_path = pathlib.Path("sentry_sdk") / "integrations" / pathlib.Path(*parts)
    import_path = ".".join(("sentry_sdk", "integrations", *parts))

    integration_dependencies = set()
    for py_file in pathlib.Path(module_path).rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        integration_dependencies.update(find_unrecognized_dependencies(tree))

    mod = None
    try:
        # If importing the integration succeeds in the current environment, assume
        # that the integration has no non-standard imports.
        mod = importlib.import_module(import_path)

    except integrations.DidNotEnable:
        # For each non-standard import, create an empty shadow module to
        # emulate an empty "agents.py" or analogous local module that
        # shadows the package.
        for dependency in integration_dependencies:
            sys.modules[dependency] = types.ModuleType(dependency)

        # Importing the integration must raise DidNotEnable, since the
        # SDK catches the exception type when attempting to activate
        # auto-enabling integrations.
        with pytest.raises(integrations.DidNotEnable):
            importlib.import_module(import_path)

        for dependency in integration_dependencies:
            del sys.modules[dependency]

    # `setup_once()` can also raise when initializing the SDK with a shadowed module.
    if mod is not None:
        # For each non-standard import, create an empty shadow module to
        # emulate an empty "agents.py" or analogous local module that
        # shadows the package.
        for dependency in integration_dependencies:
            sys.modules[dependency] = types.ModuleType(dependency)

        integration_types = [
            v
            for v in mod.__dict__.values()
            if isinstance(v, type) and issubclass(v, Integration)
        ]

        for integration in integration_types:
            # The `setup_once()` method should only raise DidNotEnable.
            try:
                integration.setup_once()
            except integrations.DidNotEnable:
                pass

        for dependency in integration_dependencies:
            del sys.modules[dependency]
