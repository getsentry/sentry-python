# coding: utf-8
import sys
import os

from hypothesis import given
import hypothesis.strategies as st

from sentry_sdk.utils import safe_repr, exceptions_from_error_tuple
from sentry_sdk._compat import text_type

any_string = st.one_of(st.binary(), st.text())


@given(x=any_string)
def test_safe_repr_never_broken_for_strings(x):
    r = safe_repr(x)
    assert isinstance(r, text_type)
    assert u"broken repr" not in r


def test_safe_repr_regressions():
    assert u"лошадь" in safe_repr(u"лошадь")


def test_abs_path():
    """Check if abs_path is actually an absolute path. This can happen either
    with eval/exec like here, or when the file in the frame is relative to
    __main__"""

    code = compile("1/0", "test.py", "exec")
    try:
        exec(code, {})
    except Exception:
        exceptions = exceptions_from_error_tuple(sys.exc_info())

    exception, = exceptions
    frames = exception["stacktrace"]["frames"]
    assert len(frames) == 2

    for frame in frames:
        assert os.path.abspath(frame["abs_path"]) == frame["abs_path"]
        assert os.path.basename(frame["filename"]) == frame["filename"]


def test_non_string_variables():
    """There is some extremely terrible code in the wild that
    inserts non-strings as variable names into `locals()`."""

    try:
        locals()[42] = True
        1 / 0
    except ZeroDivisionError:
        exceptions = exceptions_from_error_tuple(sys.exc_info())

    exception, = exceptions
    assert exception["type"] == "ZeroDivisionError"
    frame, = exception["stacktrace"]["frames"]
    assert frame["vars"]["42"] == "True"
