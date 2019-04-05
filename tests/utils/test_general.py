# coding: utf-8
import sys
import copy
import os

import pytest

from hypothesis import given
import hypothesis.strategies as st

from sentry_sdk.utils import (
    BadDsn,
    Dsn,
    safe_repr,
    exceptions_from_error_tuple,
    format_and_strip,
    strip_string,
    filename_for_module,
    handle_in_app_impl,
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
    frame1, frame2 = frames = exception["stacktrace"]["frames"]

    for frame in frames:
        assert os.path.abspath(frame["abs_path"]) == frame["abs_path"]

    assert frame1["filename"] == "tests/utils/test_general.py"
    assert frame2["filename"] == "test.py"


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


def test_filename():
    x = filename_for_module

    assert x("bogus", "bogus") == "bogus"

    assert x("os", os.__file__) == "os.py"
    assert x("pytest", pytest.__file__) == "pytest.py"

    import sentry_sdk.utils

    assert x("sentry_sdk.utils", sentry_sdk.utils.__file__) == "sentry_sdk/utils.py"


@pytest.mark.parametrize(
    "given,expected",
    [
        ("https://foobar@sentry.io/123", "https://sentry.io/api/123/store/"),
        ("https://foobar@sentry.io/bam/123", "https://sentry.io/bam/api/123/store/"),
        (
            "https://foobar@sentry.io/bam/baz/123",
            "https://sentry.io/bam/baz/api/123/store/",
        ),
    ],
)
def test_parse_dsn_paths(given, expected):
    dsn = Dsn(given)
    auth = dsn.to_auth()
    assert auth.store_api_url == expected


@pytest.mark.parametrize(
    "dsn",
    [
        "https://foobar@sentry.io"
        "https://foobar@sentry.io/"
        "https://foobar@sentry.io/asdf"
        "https://foobar@sentry.io/asdf/"
        "https://foobar@sentry.io/asdf/123/"
    ],
)
def test_parse_invalid_dsn(dsn):
    with pytest.raises(BadDsn):
        dsn = Dsn(dsn)


@pytest.mark.parametrize("empty", [None, []])
def test_in_app(empty):
    assert handle_in_app_impl(
        [{"module": "foo"}, {"module": "bar"}],
        in_app_include=["foo"],
        in_app_exclude=empty,
    ) == [{"module": "foo", "in_app": True}, {"module": "bar"}]

    assert handle_in_app_impl(
        [{"module": "foo"}, {"module": "bar"}],
        in_app_include=["foo"],
        in_app_exclude=["foo"],
    ) == [{"module": "foo", "in_app": True}, {"module": "bar"}]

    assert handle_in_app_impl(
        [{"module": "foo"}, {"module": "bar"}],
        in_app_include=empty,
        in_app_exclude=["foo"],
    ) == [{"module": "foo", "in_app": False}, {"module": "bar", "in_app": True}]
