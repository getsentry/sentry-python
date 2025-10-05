import os
import types
import tempfile
import importlib.util
import runpy
import sys

import sentry_sdk
from sentry_sdk.tracing_utils import frame_has_n_plus_one_ignore


def _make_temp_module(source):
    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    return path


def _get_frame_from_module(path):
    # Execute the file so it defines a function we can call to get a frame.
    dirname = os.path.dirname(path)
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    # The module should define `capture_frame` which returns a frame object
    return module.capture_frame()


def test_frame_has_ignore_comment_positive():
    src = """
# some header
# sentry: ignore n+1
def capture_frame():
    import inspect
    return inspect.currentframe()
"""
    path = _make_temp_module(src)
    try:
        frame = _get_frame_from_module(path)
        assert frame_has_n_plus_one_ignore(frame) is True
    finally:
        os.remove(path)


def test_frame_has_ignore_comment_negative():
    src = """
# nothing to see here
def capture_frame():
    import inspect
    return inspect.currentframe()
"""
    path = _make_temp_module(src)
    try:
        frame = _get_frame_from_module(path)
        assert frame_has_n_plus_one_ignore(frame) is False
    finally:
        os.remove(path)
