import threading
import re
import sys
from datetime import timedelta, datetime, timezone
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk._compat import PY38
from sentry_sdk.integrations import Integration
from sentry_sdk._queue import Queue
from sentry_sdk.utils import (
    Components,
    Dsn,
    datetime_from_isoformat,
    env_to_bool,
    format_timestamp,
    get_current_thread_meta,
    get_default_release,
    get_error_message,
    get_git_revision,
    is_valid_sample_rate,
    logger,
    match_regex_list,
    parse_url,
    parse_version,
    safe_str,
    sanitize_url,
    serialize_frame,
    is_sentry_url,
    _get_installed_modules,
    ensure_integration_enabled,
    to_string,
    exc_info_from_error,
    get_lines_from_file,
    package_version,
)


class TestIntegration(Integration):
    """
    Test integration for testing ensure_integration_enabled decorator.
    """

    identifier = "test"
    setup_once = mock.MagicMock()


try:
    import gevent
except ImportError:
    gevent = None


def _normalize_distribution_name(name):
    # type: (str) -> str
    """Normalize distribution name according to PEP-0503.

    See:
    https://peps.python.org/pep-0503/#normalized-names
    for more details.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


isoformat_inputs_and_datetime_outputs = (
    (
        "2021-01-01T00:00:00.000000Z",
        datetime(2021, 1, 1, tzinfo=timezone.utc),
    ),  # UTC time
    (
        "2021-01-01T00:00:00.000000",
        datetime(2021, 1, 1).astimezone(timezone.utc),
    ),  # No TZ -- assume local but convert to UTC
    (
        "2021-01-01T00:00:00Z",
        datetime(2021, 1, 1, tzinfo=timezone.utc),
    ),  # UTC - No milliseconds
    (
        "2021-01-01T00:00:00.000000+00:00",
        datetime(2021, 1, 1, tzinfo=timezone.utc),
    ),
    (
        "2021-01-01T00:00:00.000000-00:00",
        datetime(2021, 1, 1, tzinfo=timezone.utc),
    ),
    (
        "2021-01-01T00:00:00.000000+0000",
        datetime(2021, 1, 1, tzinfo=timezone.utc),
    ),
    (
        "2021-01-01T00:00:00.000000-0000",
        datetime(2021, 1, 1, tzinfo=timezone.utc),
    ),
    (
        "2020-12-31T00:00:00.000000+02:00",
        datetime(2020, 12, 31, tzinfo=timezone(timedelta(hours=2))),
    ),  # UTC+2 time
    (
        "2020-12-31T00:00:00.000000-0200",
        datetime(2020, 12, 31, tzinfo=timezone(timedelta(hours=-2))),
    ),  # UTC-2 time
    (
        "2020-12-31T00:00:00-0200",
        datetime(2020, 12, 31, tzinfo=timezone(timedelta(hours=-2))),
    ),  # UTC-2 time - no milliseconds
)


@pytest.mark.parametrize(
    ("input_str", "expected_output"),
    isoformat_inputs_and_datetime_outputs,
)
def test_datetime_from_isoformat(input_str, expected_output):
    assert datetime_from_isoformat(input_str) == expected_output, input_str


@pytest.mark.parametrize(
    ("input_str", "expected_output"),
    isoformat_inputs_and_datetime_outputs,
)
def test_datetime_from_isoformat_with_py_36_or_lower(input_str, expected_output):
    """
    `fromisoformat` was added in Python version 3.7
    """
    with mock.patch("sentry_sdk.utils.datetime") as datetime_mocked:
        datetime_mocked.fromisoformat.side_effect = AttributeError()
        datetime_mocked.strptime = datetime.strptime
        assert datetime_from_isoformat(input_str) == expected_output, input_str


@pytest.mark.parametrize(
    "env_var_value,strict,expected",
    [
        (None, True, None),
        (None, False, False),
        ("", True, None),
        ("", False, False),
        ("t", True, True),
        ("T", True, True),
        ("t", False, True),
        ("T", False, True),
        ("y", True, True),
        ("Y", True, True),
        ("y", False, True),
        ("Y", False, True),
        ("1", True, True),
        ("1", False, True),
        ("True", True, True),
        ("True", False, True),
        ("true", True, True),
        ("true", False, True),
        ("tRuE", True, True),
        ("tRuE", False, True),
        ("Yes", True, True),
        ("Yes", False, True),
        ("yes", True, True),
        ("yes", False, True),
        ("yEs", True, True),
        ("yEs", False, True),
        ("On", True, True),
        ("On", False, True),
        ("on", True, True),
        ("on", False, True),
        ("oN", True, True),
        ("oN", False, True),
        ("f", True, False),
        ("f", False, False),
        ("n", True, False),
        ("N", True, False),
        ("n", False, False),
        ("N", False, False),
        ("0", True, False),
        ("0", False, False),
        ("False", True, False),
        ("False", False, False),
        ("false", True, False),
        ("false", False, False),
        ("FaLsE", True, False),
        ("FaLsE", False, False),
        ("No", True, False),
        ("No", False, False),
        ("no", True, False),
        ("no", False, False),
        ("nO", True, False),
        ("nO", False, False),
        ("Off", True, False),
        ("Off", False, False),
        ("off", True, False),
        ("off", False, False),
        ("oFf", True, False),
        ("oFf", False, False),
        ("xxx", True, None),
        ("xxx", False, True),
    ],
)
def test_env_to_bool(env_var_value, strict, expected):
    assert env_to_bool(env_var_value, strict=strict) == expected, (
        f"Value: {env_var_value}, strict: {strict}"
    )


@pytest.mark.parametrize(
    ("url", "expected_result"),
    [
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://example.com", "http://example.com"),
        ("https://example.com", "https://example.com"),
        (
            "example.com?token=abc&sessionid=123&save=true",
            "example.com?token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
        ),
        (
            "http://example.com?token=abc&sessionid=123&save=true",
            "http://example.com?token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
        ),
        (
            "https://example.com?token=abc&sessionid=123&save=true",
            "https://example.com?token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
        ),
        (
            "http://localhost:8000/?token=abc&sessionid=123&save=true",
            "http://localhost:8000/?token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
        ),
        (
            "ftp://username:password@ftp.example.com:9876/bla/blub#foo",
            "ftp://[Filtered]:[Filtered]@ftp.example.com:9876/bla/blub#foo",
        ),
        (
            "https://username:password@example.com/bla/blub?token=abc&sessionid=123&save=true#fragment",
            "https://[Filtered]:[Filtered]@example.com/bla/blub?token=[Filtered]&sessionid=[Filtered]&save=[Filtered]#fragment",
        ),
        ("bla/blub/foo", "bla/blub/foo"),
        ("/bla/blub/foo/", "/bla/blub/foo/"),
        (
            "bla/blub/foo?token=abc&sessionid=123&save=true",
            "bla/blub/foo?token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
        ),
        (
            "/bla/blub/foo/?token=abc&sessionid=123&save=true",
            "/bla/blub/foo/?token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
        ),
    ],
)
def test_sanitize_url(url, expected_result):
    assert sanitize_url(url) == expected_result


@pytest.mark.parametrize(
    ("url", "expected_result"),
    [
        (
            "http://localhost:8000",
            Components(
                scheme="http", netloc="localhost:8000", path="", query="", fragment=""
            ),
        ),
        (
            "http://example.com",
            Components(
                scheme="http", netloc="example.com", path="", query="", fragment=""
            ),
        ),
        (
            "https://example.com",
            Components(
                scheme="https", netloc="example.com", path="", query="", fragment=""
            ),
        ),
        (
            "example.com?token=abc&sessionid=123&save=true",
            Components(
                scheme="",
                netloc="",
                path="example.com",
                query="token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
                fragment="",
            ),
        ),
        (
            "http://example.com?token=abc&sessionid=123&save=true",
            Components(
                scheme="http",
                netloc="example.com",
                path="",
                query="token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
                fragment="",
            ),
        ),
        (
            "https://example.com?token=abc&sessionid=123&save=true",
            Components(
                scheme="https",
                netloc="example.com",
                path="",
                query="token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
                fragment="",
            ),
        ),
        (
            "http://localhost:8000/?token=abc&sessionid=123&save=true",
            Components(
                scheme="http",
                netloc="localhost:8000",
                path="/",
                query="token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
                fragment="",
            ),
        ),
        (
            "ftp://username:password@ftp.example.com:9876/bla/blub#foo",
            Components(
                scheme="ftp",
                netloc="[Filtered]:[Filtered]@ftp.example.com:9876",
                path="/bla/blub",
                query="",
                fragment="foo",
            ),
        ),
        (
            "https://username:password@example.com/bla/blub?token=abc&sessionid=123&save=true#fragment",
            Components(
                scheme="https",
                netloc="[Filtered]:[Filtered]@example.com",
                path="/bla/blub",
                query="token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
                fragment="fragment",
            ),
        ),
        (
            "bla/blub/foo",
            Components(
                scheme="", netloc="", path="bla/blub/foo", query="", fragment=""
            ),
        ),
        (
            "bla/blub/foo?token=abc&sessionid=123&save=true",
            Components(
                scheme="",
                netloc="",
                path="bla/blub/foo",
                query="token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
                fragment="",
            ),
        ),
        (
            "/bla/blub/foo/?token=abc&sessionid=123&save=true",
            Components(
                scheme="",
                netloc="",
                path="/bla/blub/foo/",
                query="token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
                fragment="",
            ),
        ),
    ],
)
def test_sanitize_url_and_split(url, expected_result):
    sanitized_url = sanitize_url(url, split=True)

    assert sanitized_url.scheme == expected_result.scheme
    assert sanitized_url.netloc == expected_result.netloc
    assert sanitized_url.query == expected_result.query
    assert sanitized_url.path == expected_result.path
    assert sanitized_url.fragment == expected_result.fragment


def test_sanitize_url_remove_authority_is_false():
    url = "https://usr:pwd@example.com"
    sanitized_url = sanitize_url(url, remove_authority=False)
    assert sanitized_url == url


@pytest.mark.parametrize(
    ("url", "sanitize", "expected_url", "expected_query", "expected_fragment"),
    [
        # Test with sanitize=True
        (
            "https://example.com",
            True,
            "https://example.com",
            "",
            "",
        ),
        (
            "example.com?token=abc&sessionid=123&save=true",
            True,
            "example.com",
            "token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
            "",
        ),
        (
            "https://example.com?token=abc&sessionid=123&save=true",
            True,
            "https://example.com",
            "token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
            "",
        ),
        (
            "https://username:password@example.com/bla/blub?token=abc&sessionid=123&save=true#fragment",
            True,
            "https://[Filtered]:[Filtered]@example.com/bla/blub",
            "token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
            "fragment",
        ),
        (
            "bla/blub/foo",
            True,
            "bla/blub/foo",
            "",
            "",
        ),
        (
            "/bla/blub/foo/#baz",
            True,
            "/bla/blub/foo/",
            "",
            "baz",
        ),
        (
            "bla/blub/foo?token=abc&sessionid=123&save=true",
            True,
            "bla/blub/foo",
            "token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
            "",
        ),
        (
            "/bla/blub/foo/?token=abc&sessionid=123&save=true",
            True,
            "/bla/blub/foo/",
            "token=[Filtered]&sessionid=[Filtered]&save=[Filtered]",
            "",
        ),
        # Test with sanitize=False
        (
            "https://example.com",
            False,
            "https://example.com",
            "",
            "",
        ),
        (
            "example.com?token=abc&sessionid=123&save=true",
            False,
            "example.com",
            "token=abc&sessionid=123&save=true",
            "",
        ),
        (
            "https://example.com?token=abc&sessionid=123&save=true",
            False,
            "https://example.com",
            "token=abc&sessionid=123&save=true",
            "",
        ),
        (
            "https://username:password@example.com/bla/blub?token=abc&sessionid=123&save=true#fragment",
            False,
            "https://[Filtered]:[Filtered]@example.com/bla/blub",
            "token=abc&sessionid=123&save=true",
            "fragment",
        ),
        (
            "bla/blub/foo",
            False,
            "bla/blub/foo",
            "",
            "",
        ),
        (
            "/bla/blub/foo/#baz",
            False,
            "/bla/blub/foo/",
            "",
            "baz",
        ),
        (
            "bla/blub/foo?token=abc&sessionid=123&save=true",
            False,
            "bla/blub/foo",
            "token=abc&sessionid=123&save=true",
            "",
        ),
        (
            "/bla/blub/foo/?token=abc&sessionid=123&save=true",
            False,
            "/bla/blub/foo/",
            "token=abc&sessionid=123&save=true",
            "",
        ),
    ],
)
def test_parse_url(url, sanitize, expected_url, expected_query, expected_fragment):
    assert parse_url(url, sanitize=sanitize).url == expected_url
    assert parse_url(url, sanitize=sanitize).fragment == expected_fragment
    assert parse_url(url, sanitize=sanitize).query == expected_query


@pytest.mark.parametrize(
    "rate",
    [0.0, 0.1231, 1.0, True, False],
)
def test_accepts_valid_sample_rate(rate):
    with mock.patch.object(logger, "warning", mock.Mock()):
        result = is_valid_sample_rate(rate, source="Testing")
        assert logger.warning.called is False
        assert result is True


@pytest.mark.parametrize(
    "rate",
    [
        "dogs are great",  # wrong type
        (0, 1),  # wrong type
        {"Maisey": "Charllie"},  # wrong type
        [True, True],  # wrong type
        {0.2012},  # wrong type
        float("NaN"),  # wrong type
        None,  # wrong type
        -1.121,  # wrong value
        1.231,  # wrong value
    ],
)
def test_warns_on_invalid_sample_rate(rate, StringContaining):  # noqa: N803
    with mock.patch.object(logger, "warning", mock.Mock()):
        result = is_valid_sample_rate(rate, source="Testing")
        logger.warning.assert_any_call(StringContaining("Given sample rate is invalid"))
        assert result is False


@pytest.mark.parametrize(
    "include_source_context",
    [True, False],
)
def test_include_source_context_when_serializing_frame(include_source_context):
    frame = sys._getframe()
    result = serialize_frame(frame, include_source_context=include_source_context)

    assert include_source_context ^ ("pre_context" in result) ^ True
    assert include_source_context ^ ("context_line" in result) ^ True
    assert include_source_context ^ ("post_context" in result) ^ True


@pytest.mark.parametrize(
    "item,regex_list,expected_result",
    [
        ["", [], False],
        [None, [], False],
        ["", None, False],
        [None, None, False],
        ["some-string", [], False],
        ["some-string", None, False],
        ["some-string", ["some-string"], True],
        ["some-string", ["some"], False],
        ["some-string", ["some$"], False],  # same as above
        ["some-string", ["some.*"], True],
        ["some-string", ["Some"], False],  # we do case sensitive matching
        ["some-string", [".*string$"], True],
    ],
)
def test_match_regex_list(item, regex_list, expected_result):
    assert match_regex_list(item, regex_list) == expected_result


@pytest.mark.parametrize(
    "version,expected_result",
    [
        ["3.5.15", (3, 5, 15)],
        ["2.0.9", (2, 0, 9)],
        ["2.0.0", (2, 0, 0)],
        ["0.6.0", (0, 6, 0)],
        ["2.0.0.post1", (2, 0, 0)],
        ["2.0.0rc3", (2, 0, 0)],
        ["2.0.0rc2", (2, 0, 0)],
        ["2.0.0rc1", (2, 0, 0)],
        ["2.0.0b4", (2, 0, 0)],
        ["2.0.0b3", (2, 0, 0)],
        ["2.0.0b2", (2, 0, 0)],
        ["2.0.0b1", (2, 0, 0)],
        ["0.6beta3", (0, 6)],
        ["0.6beta2", (0, 6)],
        ["0.6beta1", (0, 6)],
        ["0.4.2b", (0, 4, 2)],
        ["0.4.2a", (0, 4, 2)],
        ["0.0.1", (0, 0, 1)],
        ["0.0.0", (0, 0, 0)],
        ["1", (1,)],
        ["1.0", (1, 0)],
        ["1.0.0", (1, 0, 0)],
        [" 1.0.0 ", (1, 0, 0)],
        ["  1.0.0   ", (1, 0, 0)],
        ["x1.0.0", None],
        ["1.0.0x", None],
        ["x1.0.0x", None],
    ],
)
def test_parse_version(version, expected_result):
    assert parse_version(version) == expected_result


@pytest.fixture
def mock_client_with_dsn_netloc():
    """
    Returns a mocked Client with a DSN netloc of "abcd1234.ingest.sentry.io".
    """
    mock_client = mock.Mock(spec=sentry_sdk.Client)
    mock_client.transport = mock.Mock(spec=sentry_sdk.Transport)
    mock_client.transport.parsed_dsn = mock.Mock(spec=Dsn)

    mock_client.transport.parsed_dsn.netloc = "abcd1234.ingest.sentry.io"

    return mock_client


@pytest.mark.parametrize(
    ["test_url", "is_sentry_url_expected"],
    [
        ["https://asdf@abcd1234.ingest.sentry.io/123456789", True],
        ["https://asdf@abcd1234.ingest.notsentry.io/123456789", False],
    ],
)
def test_is_sentry_url_true(
    test_url, is_sentry_url_expected, mock_client_with_dsn_netloc
):
    ret_val = is_sentry_url(mock_client_with_dsn_netloc, test_url)

    assert ret_val == is_sentry_url_expected


def test_is_sentry_url_no_client():
    test_url = "https://asdf@abcd1234.ingest.sentry.io/123456789"

    ret_val = is_sentry_url(None, test_url)

    assert not ret_val


@pytest.mark.parametrize(
    "error,expected_result",
    [
        ["", lambda x: safe_str(x)],
        ["some-string", lambda _: "some-string"],
    ],
)
def test_get_error_message(error, expected_result):
    with pytest.raises(BaseException) as exc_value:
        exc_value.message = error
        raise Exception
    assert get_error_message(exc_value) == expected_result(exc_value)

    with pytest.raises(BaseException) as exc_value:
        exc_value.detail = error
        raise Exception
    assert get_error_message(exc_value) == expected_result(exc_value)


def test_safe_str_fails():
    class ExplodingStr:
        def __str__(self):
            raise Exception

    obj = ExplodingStr()
    result = safe_str(obj)

    assert result == repr(obj)


def test_installed_modules_caching():
    mock_generate_installed_modules = mock.Mock()
    mock_generate_installed_modules.return_value = {"package": "1.0.0"}
    with mock.patch("sentry_sdk.utils._installed_modules", None):
        with mock.patch(
            "sentry_sdk.utils._generate_installed_modules",
            mock_generate_installed_modules,
        ):
            _get_installed_modules()
            assert mock_generate_installed_modules.called
            mock_generate_installed_modules.reset_mock()

            _get_installed_modules()
            mock_generate_installed_modules.assert_not_called()


def test_devnull_inaccessible():
    with mock.patch("sentry_sdk.utils.open", side_effect=OSError("oh no")):
        revision = get_git_revision()

    assert revision is None


def test_devnull_not_found():
    with mock.patch("sentry_sdk.utils.open", side_effect=FileNotFoundError("oh no")):
        revision = get_git_revision()

    assert revision is None


def test_default_release():
    release = get_default_release()
    assert release is not None


def test_default_release_empty_string():
    with mock.patch("sentry_sdk.utils.get_git_revision", return_value=""):
        release = get_default_release()

    assert release is None


def test_get_default_release_sentry_release_env(monkeypatch):
    monkeypatch.setenv("SENTRY_RELEASE", "sentry-env-release")
    assert get_default_release() == "sentry-env-release"


def test_get_default_release_other_release_env(monkeypatch):
    monkeypatch.setenv("SOURCE_VERSION", "other-env-release")

    with mock.patch("sentry_sdk.utils.get_git_revision", return_value=""):
        release = get_default_release()

    assert release == "other-env-release"


def test_ensure_integration_enabled_integration_enabled(sentry_init):
    def original_function():
        return "original"

    def function_to_patch():
        return "patched"

    sentry_init(integrations=[TestIntegration()])

    # Test the decorator by applying to function_to_patch
    patched_function = ensure_integration_enabled(TestIntegration, original_function)(
        function_to_patch
    )

    assert patched_function() == "patched"
    assert patched_function.__name__ == "original_function"


def test_ensure_integration_enabled_integration_disabled(sentry_init):
    def original_function():
        return "original"

    def function_to_patch():
        return "patched"

    sentry_init(integrations=[])  # TestIntegration is disabled

    # Test the decorator by applying to function_to_patch
    patched_function = ensure_integration_enabled(TestIntegration, original_function)(
        function_to_patch
    )

    assert patched_function() == "original"
    assert patched_function.__name__ == "original_function"


def test_ensure_integration_enabled_no_original_function_enabled(sentry_init):
    shared_variable = "original"

    def function_to_patch():
        nonlocal shared_variable
        shared_variable = "patched"

    sentry_init(integrations=[TestIntegration])

    # Test the decorator by applying to function_to_patch
    patched_function = ensure_integration_enabled(TestIntegration)(function_to_patch)
    patched_function()

    assert shared_variable == "patched"
    assert patched_function.__name__ == "function_to_patch"


def test_ensure_integration_enabled_no_original_function_disabled(sentry_init):
    shared_variable = "original"

    def function_to_patch():
        nonlocal shared_variable
        shared_variable = "patched"

    sentry_init(integrations=[])

    # Test the decorator by applying to function_to_patch
    patched_function = ensure_integration_enabled(TestIntegration)(function_to_patch)
    patched_function()

    assert shared_variable == "original"
    assert patched_function.__name__ == "function_to_patch"


@pytest.mark.parametrize(
    "delta,expected_milliseconds",
    [
        [timedelta(milliseconds=132), 132.0],
        [timedelta(hours=1, milliseconds=132), float(60 * 60 * 1000 + 132)],
        [timedelta(days=10), float(10 * 24 * 60 * 60 * 1000)],
        [timedelta(microseconds=100), 0.1],
    ],
)
def test_duration_in_milliseconds(delta, expected_milliseconds):
    assert delta / timedelta(milliseconds=1) == expected_milliseconds


def test_get_current_thread_meta_explicit_thread():
    results = Queue(maxsize=1)

    def target1():
        pass

    def target2():
        results.put(get_current_thread_meta(thread1))

    thread1 = threading.Thread(target=target1)
    thread1.start()

    thread2 = threading.Thread(target=target2)
    thread2.start()

    thread2.join()
    thread1.join()

    assert (thread1.ident, thread1.name) == results.get(timeout=1)


def test_get_current_thread_meta_bad_explicit_thread():
    thread = "fake thread"

    main_thread = threading.main_thread()

    assert (main_thread.ident, main_thread.name) == get_current_thread_meta(thread)


@pytest.mark.skipif(gevent is None, reason="gevent not enabled")
def test_get_current_thread_meta_gevent_in_thread():
    results = Queue(maxsize=1)

    def target():
        with mock.patch("sentry_sdk.utils.is_gevent", side_effect=[True]):
            job = gevent.spawn(get_current_thread_meta)
            job.join()
            results.put(job.value)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    assert (thread.ident, None) == results.get(timeout=1)


@pytest.mark.skipif(gevent is None, reason="gevent not enabled")
def test_get_current_thread_meta_gevent_in_thread_failed_to_get_hub():
    results = Queue(maxsize=1)

    def target():
        with mock.patch("sentry_sdk.utils.is_gevent", side_effect=[True]):
            with mock.patch(
                "sentry_sdk.utils.get_gevent_hub", side_effect=["fake gevent hub"]
            ):
                job = gevent.spawn(get_current_thread_meta)
                job.join()
                results.put(job.value)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    assert (thread.ident, thread.name) == results.get(timeout=1)


def test_get_current_thread_meta_running_thread():
    results = Queue(maxsize=1)

    def target():
        results.put(get_current_thread_meta())

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    assert (thread.ident, thread.name) == results.get(timeout=1)


def test_get_current_thread_meta_bad_running_thread():
    results = Queue(maxsize=1)

    def target():
        with mock.patch("threading.current_thread", side_effect=["fake thread"]):
            results.put(get_current_thread_meta())

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()

    main_thread = threading.main_thread()
    assert (main_thread.ident, main_thread.name) == results.get(timeout=1)


def test_get_current_thread_meta_main_thread():
    results = Queue(maxsize=1)

    def target():
        # mock that somehow the current thread doesn't exist
        with mock.patch("threading.current_thread", side_effect=[None]):
            results.put(get_current_thread_meta())

    main_thread = threading.main_thread()

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    assert (main_thread.ident, main_thread.name) == results.get(timeout=1)


@pytest.mark.skipif(PY38, reason="Flakes a lot on 3.8 in CI.")
def test_get_current_thread_meta_failed_to_get_main_thread():
    results = Queue(maxsize=1)

    def target():
        with mock.patch("threading.current_thread", side_effect=["fake thread"]):
            with mock.patch("threading.current_thread", side_effect=["fake thread"]):
                results.put(get_current_thread_meta())

    main_thread = threading.main_thread()

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    assert (main_thread.ident, main_thread.name) == results.get(timeout=1)


@pytest.mark.parametrize(
    ("datetime_object", "expected_output"),
    (
        (
            datetime(2021, 1, 1, tzinfo=timezone.utc),
            "2021-01-01T00:00:00.000000Z",
        ),  # UTC time
        (
            datetime(2021, 1, 1, tzinfo=timezone(timedelta(hours=2))),
            "2020-12-31T22:00:00.000000Z",
        ),  # UTC+2 time
        (
            datetime(2021, 1, 1, tzinfo=timezone(timedelta(hours=-7))),
            "2021-01-01T07:00:00.000000Z",
        ),  # UTC-7 time
        (
            datetime(2021, 2, 3, 4, 56, 7, 890123, tzinfo=timezone.utc),
            "2021-02-03T04:56:07.890123Z",
        ),  # UTC time all non-zero fields
    ),
)
def test_format_timestamp(datetime_object, expected_output):
    formatted = format_timestamp(datetime_object)

    assert formatted == expected_output


def test_format_timestamp_naive():
    datetime_object = datetime(2021, 1, 1)
    timestamp_regex = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{6}Z"

    # Ensure that some timestamp is returned, without error. We currently treat these as local time, but this is an
    # implementation detail which we should not assert here.
    assert re.fullmatch(timestamp_regex, format_timestamp(datetime_object))


def test_qualname_from_function_inner_function():
    def test_function(): ...

    assert (
        sentry_sdk.utils.qualname_from_function(test_function)
        == "tests.test_utils.test_qualname_from_function_inner_function.<locals>.test_function"
    )


def test_qualname_from_function_none_name():
    def test_function(): ...

    test_function.__module__ = None

    assert (
        sentry_sdk.utils.qualname_from_function(test_function)
        == "test_qualname_from_function_none_name.<locals>.test_function"
    )


def test_to_string_unicode_decode_error():
    class BadStr:
        def __str__(self):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "reason")

    obj = BadStr()
    result = to_string(obj)
    assert result == repr(obj)[1:-1]


def test_exc_info_from_error_dont_get_an_exc():
    class NotAnException:
        pass

    with pytest.raises(ValueError) as exc:
        exc_info_from_error(NotAnException())

    assert "Expected Exception object to report, got <class" in str(exc.value)


def test_get_lines_from_file_handle_linecache_errors():
    expected_result = ([], None, [])

    class Loader:
        @staticmethod
        def get_source(module):
            raise IOError("something went wrong")

    result = get_lines_from_file("filename", 10, loader=Loader())
    assert result == expected_result

    with mock.patch(
        "sentry_sdk.utils.linecache.getlines",
        side_effect=OSError("something went wrong"),
    ):
        result = get_lines_from_file("filename", 10)
        assert result == expected_result

    lines = ["line1", "line2", "line3"]

    def fake_getlines(filename):
        return lines

    with mock.patch("sentry_sdk.utils.linecache.getlines", fake_getlines):
        result = get_lines_from_file("filename", 10)
        assert result == expected_result


def test_package_version_is_none():
    assert package_version("non_existent_package") is None
