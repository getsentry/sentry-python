"""Self-contained Python interpreter discovery."""

from __future__ import annotations

from importlib.metadata import version

from ._cache import ContentStore, DiskCache, PyInfoCache
from ._discovery import get_interpreter
from ._py_info import PythonInfo
from ._py_spec import PythonSpec
from ._specifier import SimpleSpecifier, SimpleSpecifierSet, SimpleVersion

__version__ = version("python-discovery")

__all__ = [
    "ContentStore",
    "DiskCache",
    "PyInfoCache",
    "PythonInfo",
    "PythonSpec",
    "SimpleSpecifier",
    "SimpleSpecifierSet",
    "SimpleVersion",
    "__version__",
    "get_interpreter",
]
