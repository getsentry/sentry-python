# coding: utf-8
import sys
import os

import pytest


from sentry_sdk.utils import (
    BadDsn,
    Dsn,
    safe_repr,
    exceptions_from_error_tuple,
    filename_for_module,
    handle_in_app_impl,
    iter_event_stacktraces,
)
from sentry_sdk._compat import text_type


try:
    from hypothesis import given
    import hypothesis.strategies as st
except ImportError:
    pass
else:
    any_string = st.one_of(st.binary(), st.text())

    @given(x=any_string)
    def test_safe_repr_never_broken_for_strings(x):
        r = safe_repr(x)
        assert isinstance(r, text_type)
        assert u"broken repr" not in r


def test_safe_repr_regressions():
    assert u"лошадь" in safe_repr(u"лошадь")


@pytest.mark.xfail(
    sys.version_info < (3,),
    reason="Fixing this in Python 2 would break other behaviors",
)
@pytest.mark.parametrize("prefix", (u"", u"abcd", u"лошадь"))
@pytest.mark.parametrize("character", u"\x00\x07\x1b\n")
def test_safe_repr_non_printable(prefix, character):
    """Check that non-printable characters are escaped"""
    string = prefix + character
    assert character not in safe_repr(string)
    assert character not in safe_repr(string.encode("utf-8"))


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


def test_iter_stacktraces():
    assert set(
        iter_event_stacktraces(
            {
                "threads": {"values": [{"stacktrace": 1}]},
                "stacktrace": 2,
                "exception": {"values": [{"stacktrace": 3}]},
            }
        )
    ) == {1, 2, 3}
