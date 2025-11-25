import sys
import os

import pytest
from sentry_sdk.ai.utils import _normalize_data


from sentry_sdk.utils import (
    BadDsn,
    Dsn,
    safe_repr,
    exceptions_from_error_tuple,
    filename_for_module,
    iter_event_stacktraces,
    to_base64,
    from_base64,
    set_in_app_in_frames,
    strip_string,
    AnnotatedValue,
)
from sentry_sdk.consts import EndpointType


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
        assert isinstance(r, str)
        assert "broken repr" not in r


def test_safe_repr_regressions():
    assert "лошадь" in safe_repr("лошадь")


@pytest.mark.parametrize("prefix", ("", "abcd", "лошадь"))
@pytest.mark.parametrize("character", "\x00\x07\x1b\n")
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

    (exception,) = exceptions
    frame1, frame2 = frames = exception["stacktrace"]["frames"]

    for frame in frames:
        assert os.path.abspath(frame["abs_path"]) == frame["abs_path"]

    assert frame1["filename"] == "tests/utils/test_general.py"
    assert frame2["filename"] == "test.py"


def test_filename():
    x = filename_for_module

    assert x("bogus", "bogus") == "bogus"

    assert x("bogus", "bogus.pyc") == "bogus.py"

    assert x("os", os.__file__) == "os.py"

    assert x("foo.bar", "path/to/foo/bar.py") == "path/to/foo/bar.py"

    import sentry_sdk.utils

    assert x("sentry_sdk.utils", sentry_sdk.utils.__file__) == "sentry_sdk/utils.py"


def test_filename_module_file_is_none():
    class DummyModule:
        __file__ = None

    os.sys.modules["foo"] = DummyModule()

    assert filename_for_module("foo.bar", "path/to/foo/bar.py") == "path/to/foo/bar.py"


@pytest.mark.parametrize(
    "given,expected_envelope",
    [
        (
            "https://foobar@sentry.io/123",
            "https://sentry.io/api/123/envelope/",
        ),
        (
            "https://foobar@sentry.io/bam/123",
            "https://sentry.io/bam/api/123/envelope/",
        ),
        (
            "https://foobar@sentry.io/bam/baz/123",
            "https://sentry.io/bam/baz/api/123/envelope/",
        ),
    ],
)
def test_parse_dsn_paths(given, expected_envelope):
    dsn = Dsn(given)
    auth = dsn.to_auth()
    assert auth.get_api_url() == expected_envelope
    assert auth.get_api_url(EndpointType.ENVELOPE) == expected_envelope


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


@pytest.mark.parametrize(
    "dsn,error_message",
    [
        ("foo://barbaz@sentry.io", "Unsupported scheme 'foo'"),
        ("https://foobar@", "Missing hostname"),
        ("https://@sentry.io", "Missing public key"),
    ],
)
def test_dsn_validations(dsn, error_message):
    with pytest.raises(BadDsn) as e:
        dsn = Dsn(dsn)

    assert str(e.value) == error_message


@pytest.mark.parametrize(
    "frame,in_app_include,in_app_exclude,project_root,resulting_frame",
    [
        [
            {
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
            },
            None,
            None,
            None,
            {
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": False,
            },
        ],
        [
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
            },
            None,
            None,
            None,
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": False,
            },
        ],
        [
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": True,
            },
            None,
            None,
            None,
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": True,
            },
        ],
        [
            {
                "abs_path": "C:\\Users\\winuser\\AppData\\Roaming\\Python\\Python35\\site-packages\\fastapi\\routing.py",
            },
            None,
            None,
            None,
            {
                "abs_path": "C:\\Users\\winuser\\AppData\\Roaming\\Python\\Python35\\site-packages\\fastapi\\routing.py",
                "in_app": False,
            },
        ],
        [
            {
                "module": "fastapi.routing",
                "abs_path": "/usr/lib/python2.7/dist-packages/fastapi/routing.py",
            },
            None,
            None,
            None,
            {
                "module": "fastapi.routing",
                "abs_path": "/usr/lib/python2.7/dist-packages/fastapi/routing.py",
                "in_app": False,
            },
        ],
        [
            {
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
            None,
            None,
            None,
            {
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
        ],
        [
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
            None,
            None,
            None,
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
        ],
        # include
        [
            {
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
            },
            ["fastapi"],
            None,
            None,
            {
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": False,  # because there is no module set
            },
        ],
        [
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
            },
            ["fastapi"],
            None,
            None,
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": True,
            },
        ],
        [
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": False,
            },
            ["fastapi"],
            None,
            None,
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": False,
            },
        ],
        [
            {
                "abs_path": "C:\\Users\\winuser\\AppData\\Roaming\\Python\\Python35\\site-packages\\fastapi\\routing.py",
            },
            ["fastapi"],
            None,
            None,
            {
                "abs_path": "C:\\Users\\winuser\\AppData\\Roaming\\Python\\Python35\\site-packages\\fastapi\\routing.py",
                "in_app": False,  # because there is no module set
            },
        ],
        [
            {
                "module": "fastapi.routing",
                "abs_path": "/usr/lib/python2.7/dist-packages/fastapi/routing.py",
            },
            ["fastapi"],
            None,
            None,
            {
                "module": "fastapi.routing",
                "abs_path": "/usr/lib/python2.7/dist-packages/fastapi/routing.py",
                "in_app": True,
            },
        ],
        [
            {
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
            ["fastapi"],
            None,
            None,
            {
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
        ],
        [
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
            ["fastapi"],
            None,
            None,
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
        ],
        # exclude
        [
            {
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
            },
            None,
            ["main"],
            None,
            {
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": False,
            },
        ],
        [
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
            },
            None,
            ["main"],
            None,
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": False,
            },
        ],
        [
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": True,
            },
            None,
            ["main"],
            None,
            {
                "module": "fastapi.routing",
                "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
                "in_app": True,
            },
        ],
        [
            {
                "abs_path": "C:\\Users\\winuser\\AppData\\Roaming\\Python\\Python35\\site-packages\\fastapi\\routing.py",
            },
            None,
            ["main"],
            None,
            {
                "abs_path": "C:\\Users\\winuser\\AppData\\Roaming\\Python\\Python35\\site-packages\\fastapi\\routing.py",
                "in_app": False,
            },
        ],
        [
            {
                "module": "fastapi.routing",
                "abs_path": "/usr/lib/python2.7/dist-packages/fastapi/routing.py",
            },
            None,
            ["main"],
            None,
            {
                "module": "fastapi.routing",
                "abs_path": "/usr/lib/python2.7/dist-packages/fastapi/routing.py",
                "in_app": False,
            },
        ],
        [
            {
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
            None,
            ["main"],
            None,
            {
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
        ],
        [
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
            None,
            ["main"],
            None,
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
                "in_app": False,
            },
        ],
        [
            {
                "module": "fastapi.routing",
            },
            None,
            None,
            None,
            {
                "module": "fastapi.routing",
            },
        ],
        [
            {
                "module": "fastapi.routing",
            },
            ["fastapi"],
            None,
            None,
            {
                "module": "fastapi.routing",
                "in_app": True,
            },
        ],
        [
            {
                "module": "fastapi.routing",
            },
            None,
            ["fastapi"],
            None,
            {
                "module": "fastapi.routing",
                "in_app": False,
            },
        ],
        # with project_root set
        [
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
            None,
            None,
            "/home/ubuntu/fastapi",
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
                "in_app": True,
            },
        ],
        [
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
            ["main"],
            None,
            "/home/ubuntu/fastapi",
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
                "in_app": True,
            },
        ],
        [
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
            },
            None,
            ["main"],
            "/home/ubuntu/fastapi",
            {
                "module": "main",
                "abs_path": "/home/ubuntu/fastapi/main.py",
                "in_app": False,
            },
        ],
    ],
)
def test_set_in_app_in_frames(
    frame, in_app_include, in_app_exclude, project_root, resulting_frame
):
    new_frames = set_in_app_in_frames(
        [frame],
        in_app_include=in_app_include,
        in_app_exclude=in_app_exclude,
        project_root=project_root,
    )

    assert new_frames[0] == resulting_frame


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


@pytest.mark.parametrize(
    ("original", "base64_encoded"),
    [
        # ascii only
        ("Dogs are great!", "RG9ncyBhcmUgZ3JlYXQh"),
        # emoji
        ("🐶", "8J+Qtg=="),
        # non-ascii
        (
            "Καλό κορίτσι, Μάιζεϊ!",
            "zprOsc67z4wgzrrOv8+Bzq/PhM+DzrksIM6czqzOuc62zrXPiiE=",
        ),
        # mix of ascii and non-ascii
        (
            "Of margir hundar! Ég geri ráð fyrir að ég þurfi stærra rúm.",
            "T2YgbWFyZ2lyIGh1bmRhciEgw4lnIGdlcmkgcsOhw7AgZnlyaXIgYcOwIMOpZyDDvnVyZmkgc3TDpnJyYSByw7ptLg==",
        ),
    ],
)
def test_successful_base64_conversion(original, base64_encoded):
    # all unicode characters should be handled correctly
    assert to_base64(original) == base64_encoded
    assert from_base64(base64_encoded) == original

    # "to" and "from" should be inverses
    assert from_base64(to_base64(original)) == original
    assert to_base64(from_base64(base64_encoded)) == base64_encoded


@pytest.mark.parametrize(
    "input",
    [
        1231,  # incorrect type
        True,  # incorrect type
        [],  # incorrect type
        {},  # incorrect type
        None,  # incorrect type
        "yayfordogs",  # wrong length
        "#dog",  # invalid ascii character
        "🐶",  # non-ascii character
    ],
)
def test_failed_base64_conversion(input):
    # conversion from base64 should fail if given input of the wrong type or
    # input which isn't a valid base64 string
    assert from_base64(input) is None

    # any string can be converted to base64, so only type errors will cause
    # failures
    if not isinstance(input, str):
        assert to_base64(input) is None


@pytest.mark.parametrize(
    "input,max_length,result",
    [
        [None, None, None],
        ["a" * 256, None, "a" * 256],
        [
            "a" * 257,
            256,
            AnnotatedValue(
                value="a" * 253 + "...",
                metadata={"len": 257, "rem": [["!limit", "x", 253, 256]]},
            ),
        ],
        ["éééé", None, "éééé"],
        [
            "éééé",
            5,
            AnnotatedValue(
                value="é...", metadata={"len": 8, "rem": [["!limit", "x", 2, 5]]}
            ),
        ],
        [
            "\udfff\udfff\udfff\udfff\udfff\udfff",
            5,
            AnnotatedValue(
                value="\udfff\udfff...",
                metadata={
                    "len": 6,
                    "rem": [["!limit", "x", 5 - 3, 5]],
                },
            ),
        ],
    ],
)
def test_strip_string(input, max_length, result):
    assert strip_string(input, max_length) == result


def test_normalize_data_with_pydantic_class():
    """Test that _normalize_data handles Pydantic model classes"""
    class TestClass:
        name: str = None

        def __init__(self, name: str):
            self.name = name
        
        def model_dump(self):
            return {"name": self.name}
    
    # Test with class (should NOT call model_dump())
    result = _normalize_data(TestClass)
    assert result == "<ClassType: TestClass>"
    
    # Test with instance (should call model_dump())
    instance = TestClass(name="test")
    result = _normalize_data(instance)
    assert result == {"name": "test"}
    
    # Test with dict containing class
    result = _normalize_data({"schema": TestClass, "count": 5})
    assert result == {"schema": "<ClassType: TestClass>", "count": 5}
