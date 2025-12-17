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
        }

        metafunc.parametrize(
            "integration_submodule_name",
            # Temporarily skip some integrations
            submodule_names
            - {
                "grpc",
                "litellm",
                "opentelemetry",
                "pure_eval",
                "ray",
                "trytond",
                "typer",
            },
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
    module_path = f"sentry_sdk.integrations.{integration_submodule_name}"
    try:
        # If importing the integration succeeds in the current environment, assume
        # that the integration has no non-standard imports.
        importlib.import_module(module_path)
        return
    except integrations.DidNotEnable:
        spec = importlib.util.find_spec(module_path)
        source = pathlib.Path(spec.origin).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=spec.origin)
        integration_dependencies = find_unrecognized_dependencies(tree)

        # For each non-standard import, create an empty shadow module to
        # emulate an empty "agents.py" or analogous local module that
        # shadows the package.
        for dependency in integration_dependencies:
            sys.modules[dependency] = types.ModuleType(dependency)

        # Importing the integration must raise DidNotEnable, since the
        # SDK catches the exception type when attempting to activate
        # auto-enabling integrations.
        with pytest.raises(integrations.DidNotEnable):
            importlib.import_module(module_path)

        for dependency in integration_dependencies:
            del sys.modules[dependency]
