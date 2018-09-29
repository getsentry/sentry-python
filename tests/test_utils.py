# coding: utf-8
import sys
import os

import pytest

from hypothesis import given
import hypothesis.strategies as st

from sentry_sdk.utils import (
    safe_repr,
    exceptions_from_error_tuple,
    format_and_strip,
    strip_string,
)
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


def test_format_and_strip():
    max_length = None

    def x(template, params):
        return format_and_strip(
            template,
            params,
            strip_string=lambda x: strip_string(x, max_length=max_length),
        )

    max_length = 3

    assert x("", []) == ""
    assert x("f", []) == "f"
    pytest.raises(ValueError, lambda: x("%s", []))

    # Don't raise errors on leftover params, some django extensions send too
    # many SQL parameters.
    assert x("", [123]) == ""
    assert x("foo%s", ["bar"]) == "foobar"

    rv = x("foo%s", ["baer"])
    assert rv.value == "foo..."
    assert rv.metadata == {"len": 7, "rem": [["!limit", "x", 3, 6]]}

    rv = x("foo%sbar%s", ["baer", "boor"])
    assert rv.value == "foo...bar..."
    assert rv.metadata == {
        "len": 14,
        "rem": [["!limit", "x", 3, 6], ["!limit", "x", 9, 12]],
    }
