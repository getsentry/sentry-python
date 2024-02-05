import pytest
import re
import sys
from datetime import timedelta

from sentry_sdk._compat import duration_in_milliseconds
from sentry_sdk.utils import (
    Components,
    Dsn,
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
)

import sentry_sdk

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3

try:
    # Python 3
    FileNotFoundError
except NameError:
    # Python 2
    FileNotFoundError = IOError


def _normalize_distribution_name(name):
    # type: (str) -> str
    """Normalize distribution name according to PEP-0503.

    See:
    https://peps.python.org/pep-0503/#normalized-names
    for more details.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


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
    # sort parts because old Python versions (<3.6) don't preserve order
    sanitized_url = sanitize_url(url)
    parts = sorted(re.split(r"\&|\?|\#", sanitized_url))
    expected_parts = sorted(re.split(r"\&|\?|\#", expected_result))

    assert parts == expected_parts


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
    # sort query because old Python versions (<3.6) don't preserve order
    query = sorted(sanitized_url.query.split("&"))
    expected_query = sorted(expected_result.query.split("&"))

    assert sanitized_url.scheme == expected_result.scheme
    assert sanitized_url.netloc == expected_result.netloc
    assert query == expected_query
    assert sanitized_url.path == expected_result.path
    assert sanitized_url.fragment == expected_result.fragment


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

    # sort parts because old Python versions (<3.6) don't preserve order
    sanitized_query = parse_url(url, sanitize=sanitize).query
    query_parts = sorted(re.split(r"\&|\?|\#", sanitized_query))
    expected_query_parts = sorted(re.split(r"\&|\?|\#", expected_query))

    assert query_parts == expected_query_parts


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
def mock_hub_with_dsn_netloc():
    """
    Returns a mocked hub with a DSN netloc of "abcd1234.ingest.sentry.io".
    """

    mock_hub = mock.Mock(spec=sentry_sdk.Hub)
    mock_hub.client = mock.Mock(spec=sentry_sdk.Client)
    mock_hub.client.transport = mock.Mock(spec=sentry_sdk.Transport)
    mock_hub.client.transport.parsed_dsn = mock.Mock(spec=Dsn)

    mock_hub.client.transport.parsed_dsn.netloc = "abcd1234.ingest.sentry.io"

    return mock_hub


@pytest.mark.parametrize(
    ["test_url", "is_sentry_url_expected"],
    [
        ["https://asdf@abcd1234.ingest.sentry.io/123456789", True],
        ["https://asdf@abcd1234.ingest.notsentry.io/123456789", False],
    ],
)
def test_is_sentry_url_true(test_url, is_sentry_url_expected, mock_hub_with_dsn_netloc):
    ret_val = is_sentry_url(mock_hub_with_dsn_netloc, test_url)

    assert ret_val == is_sentry_url_expected


def test_is_sentry_url_no_client():
    hub = mock.Mock()
    hub.client = None

    test_url = "https://asdf@abcd1234.ingest.sentry.io/123456789"

    ret_val = is_sentry_url(hub, test_url)

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


def test_installed_modules():
    try:
        from importlib.metadata import distributions, version

        importlib_available = True
    except ImportError:
        importlib_available = False

    try:
        import pkg_resources

        pkg_resources_available = True
    except ImportError:
        pkg_resources_available = False

    installed_distributions = {
        _normalize_distribution_name(dist): version
        for dist, version in _get_installed_modules().items()
    }

    if importlib_available:
        importlib_distributions = {
            _normalize_distribution_name(dist.metadata["Name"]): version(
                dist.metadata["Name"]
            )
            for dist in distributions()
            if dist.metadata["Name"] is not None
            and version(dist.metadata["Name"]) is not None
        }
        assert installed_distributions == importlib_distributions

    elif pkg_resources_available:
        pkg_resources_distributions = {
            _normalize_distribution_name(dist.key): dist.version
            for dist in pkg_resources.working_set
        }
        assert installed_distributions == pkg_resources_distributions
    else:
        pytest.fail("Neither importlib nor pkg_resources is available")


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


@pytest.mark.parametrize(
    "timedelta,expected_milliseconds",
    [
        [timedelta(milliseconds=132), 132.0],
        [timedelta(hours=1, milliseconds=132), float(60 * 60 * 1000 + 132)],
        [timedelta(days=10), float(10 * 24 * 60 * 60 * 1000)],
        [timedelta(microseconds=100), 0.1],
    ],
)
def test_duration_in_milliseconds(timedelta, expected_milliseconds):
    assert duration_in_milliseconds(timedelta) == expected_milliseconds
