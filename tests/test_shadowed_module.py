import sys
import ast
import types
import pkgutil
import importlib
import pathlib
import pytest

from sentry_sdk import integrations
from sentry_sdk.integrations import _DEFAULT_INTEGRATIONS, Integration


def unrecognized_dependencies(tree):
    """
    Finds unrecognized imports in the AST for a Python module. In an empty
    environment the set of non-standard library modules is returned.
    """
    unrecognized_dependency = set()
    package_name = lambda name: name.split(".")[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = package_name(alias.name)

                try:
                    if not importlib.util.find_spec(root):
                        unrecognized_dependency.add(root)
                except ValueError:
                    continue

        elif isinstance(node, ast.ImportFrom):
            # if node.level is not 0 the import is relative
            if node.level > 0 or node.module is None:
                continue

            root = package_name(node.module)

            try:
                if not importlib.util.find_spec(root):
                    unrecognized_dependency.add(root)
            except ValueError:
                continue

    return unrecognized_dependency


def test_shadowed_module(sentry_init):
    """
    Check that importing integrations for third-party module raises an
    DidNotEnable exception when the associated module is shadowed by an empty
    module.

    An integration is determined to be for a third-party module if it cannot
    be imported in the environment in which the tests run.
    """
    for _, submodule_name, _ in pkgutil.walk_packages(integrations.__path__):
        module_path = f"sentry_sdk.integrations.{submodule_name}"

        try:
            importlib.import_module(module_path)
            continue
        except integrations.DidNotEnable:
            spec = importlib.util.find_spec(module_path)
            source = pathlib.Path(spec.origin).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=spec.origin)
            integration_dependencies = unrecognized_dependencies(tree)
            for dependency in integration_dependencies:
                sys.modules[dependency] = types.ModuleType(dependency)
            with pytest.raises(integrations.DidNotEnable):
                importlib.import_module(module_path)
