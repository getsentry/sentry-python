import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk._queue import Queue
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.utils import (
    Components,
    Dsn,
    _get_installed_modules,
    datetime_from_isoformat,
    ensure_integration_enabled,
    env_to_bool,
    exc_info_from_error,
    format_timestamp,
    get_current_thread_meta,
    get_default_release,
    get_error_message,
    get_git_revision,
    get_lines_from_file,
    is_sentry_url,
    is_valid_sample_rate,
    logger,
    match_regex_list,
    package_version,
    parse_url,
    parse_version,
    safe_serialize,
    safe_str,
    sanitize_url,
    serialize_frame,
    to_string,
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


def _normalize_distribution_name(name: str) -> str:
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
        # One canonical form per truthy word...
        ("t", True, True),
        ("y", True, True),
        ("1", True, True),
        ("true", True, True),
        ("yes", True, True),
        ("on", True, True),
        # ...plus mixed-case variants to prove case-insensitivity (same
        # .lower() code path for all words, so one per result is enough)
        ("tRuE", True, True),
        ("On", False, True),
        # One canonical form per falsy word...
        ("f", True, False),
        ("n", True, False),
        ("0", True, False),
        ("false", True, False),
        ("no", True, False),
        ("off", True, False),
        # ...plus a mixed-case variant and a strict=False parity check
        ("FaLsE", True, False),
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
    [0.0, 1.0, True],
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
        None,  # wrong type
        float("NaN"),  # wrong type (edge: float, but not a valid rate)
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
    "options,include_source_context,expected_source_context",
    [
        pytest.param({}, True, True, id="no_data_collection-include_true"),
        pytest.param({}, False, False, id="no_data_collection-include_false"),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            False,
            True,
            id="data_collection-spec_default_overrides_include_false",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"frame_context_lines": 3}}},
            True,
            True,
            id="data_collection-frame_context_lines_3",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"frame_context_lines": 0}}},
            True,
            False,
            id="data_collection-frame_context_lines_0_overrides_include_true",
        ),
    ],
)
def test_include_source_context_when_serializing_frame(
    sentry_init, options, include_source_context, expected_source_context
):
    sentry_init(**options)

    frame = sys._getframe()
    result = serialize_frame(frame, include_source_context=include_source_context)

    assert ("pre_context" in result) is expected_source_context
    assert ("context_line" in result) is expected_source_context
    assert ("post_context" in result) is expected_source_context


def _frame_with_locals():
    safe_value = "not sensitive"  # noqa: F841
    password = "ada123"  # noqa: F841
    api_key = "abc123"  # noqa: F841
    nickname = "Beans"  # noqa: F841
    return sys._getframe()


@pytest.mark.parametrize(
    "data_collection,include_local_variables,expected_vars",
    [
        pytest.param(
            {"stack_frame_variables": True},
            False,
            True,
            id="data_collection_stack_frame_variables_true_overrides_include_false",
        ),
        pytest.param(
            {"stack_frame_variables": False},
            True,
            False,
            id="data_collection_stack_frame_variables_false_overrides_include_true",
        ),
        pytest.param(
            {},
            False,
            True,
            id="data_collection_stack_frame_variables_spec_default_is_true",
        ),
    ],
)
def test_stack_frame_variables_bool_when_serializing_frame(
    sentry_init, data_collection, include_local_variables, expected_vars
):
    sentry_init(_experiments={"data_collection": data_collection})

    result = serialize_frame(
        _frame_with_locals(), include_local_variables=include_local_variables
    )

    assert ("vars" in result) is expected_vars


def test_stack_frame_variables_true_does_not_filter_sensitive_locals(sentry_init):
    sentry_init(_experiments={"data_collection": {"stack_frame_variables": True}})

    result = serialize_frame(_frame_with_locals())

    assert result["vars"]["safe_value"] == "'not sensitive'"
    assert result["vars"]["password"] == "'ada123'"


@pytest.mark.parametrize(
    "behaviour,expected_vars",
    [
        pytest.param(
            {"mode": "denylist"},
            {
                "safe_value": "'not sensitive'",
                "password": "'[Filtered]'",
                "api_key": "'[Filtered]'",
                "nickname": "'Beans'",
            },
            id="data_collection_stack_frame_variables_denylist_builtin_terms_only",
        ),
        pytest.param(
            {"mode": "denylist", "terms": ["nickname"]},
            {
                "safe_value": "'not sensitive'",
                "password": "'[Filtered]'",
                "api_key": "'[Filtered]'",
                "nickname": "'[Filtered]'",
            },
            id="data_collection_stack_frame_variables_denylist_user_terms",
        ),
        pytest.param(
            {"mode": "allowlist", "terms": ["safe"]},
            {
                "safe_value": "'not sensitive'",
                "password": "'[Filtered]'",
                "api_key": "'[Filtered]'",
                "nickname": "'[Filtered]'",
            },
            id="data_collection_stack_frame_variables_allowlist_user_terms",
        ),
        pytest.param(
            {"mode": "allowlist", "terms": ["safe", "api_key"]},
            {
                "safe_value": "'not sensitive'",
                "password": "'[Filtered]'",
                "api_key": "'[Filtered]'",
                "nickname": "'[Filtered]'",
            },
            id="data_collection_stack_frame_variables_allowlist_cannot_allow_sensitive_term",
        ),
    ],
)
def test_stack_frame_variables_filtering_when_serializing_frame(
    sentry_init, behaviour, expected_vars
):
    sentry_init(_experiments={"data_collection": {"stack_frame_variables": behaviour}})

    result = serialize_frame(_frame_with_locals())

    assert result["vars"] == expected_vars


def test_stack_frame_variables_off_omits_vars(sentry_init):
    sentry_init(
        _experiments={"data_collection": {"stack_frame_variables": {"mode": "off"}}}
    )

    result = serialize_frame(_frame_with_locals())

    assert "vars" not in result


def test_stack_frame_variables_omits_vars_when_frame_has_no_locals(sentry_init):
    def _frame_without_locals():
        return sys._getframe()

    sentry_init(
        _experiments={
            "data_collection": {"stack_frame_variables": {"mode": "denylist"}}
        }
    )

    result = serialize_frame(_frame_without_locals())

    assert "vars" not in result


def test_stack_frame_variables_filtering_uses_custom_repr(sentry_init):
    sentry_init(
        _experiments={
            "data_collection": {"stack_frame_variables": {"mode": "denylist"}}
        }
    )

    def custom_repr(value):
        return "CUSTOM" if value == "not sensitive" else None

    result = serialize_frame(_frame_with_locals(), custom_repr=custom_repr)

    assert result["vars"]["safe_value"] == "CUSTOM"
    assert result["vars"]["password"] == "'[Filtered]'"


@pytest.mark.parametrize(
    "options,include_local_variables,expected_vars",
    [
        pytest.param(
            {},
            True,
            True,
            id="no_data_collection-include_local_variables_true",
        ),
        pytest.param(
            {},
            False,
            False,
            id="no_data_collection-include_local_variables_false",
        ),
    ],
)
def test_include_local_variables_when_data_collection_is_unset(
    sentry_init, options, include_local_variables, expected_vars
):
    sentry_init(**options)

    result = serialize_frame(
        _frame_with_locals(), include_local_variables=include_local_variables
    )

    assert ("vars" in result) is expected_vars


def test_data_collection_stack_frame_variables_overrides_include_local_variables_option(
    sentry_init, capture_events
):
    sentry_init(
        include_local_variables=False,
        _experiments={"data_collection": {"stack_frame_variables": True}},
    )
    events = capture_events()

    def raise_with_locals():
        safe_value = "not sensitive"  # noqa: F841
        raise ValueError("boom")

    try:
        raise_with_locals()
    except ValueError:
        sentry_sdk.capture_exception()

    (event,) = events
    frame = event["exception"]["values"][0]["stacktrace"]["frames"][-1]
    assert frame["vars"]["safe_value"] == "'not sensitive'"


def test_data_collection_stack_frame_variables_filtering_applies_to_captured_exception(
    sentry_init, capture_events
):
    sentry_init(
        _experiments={
            "data_collection": {
                "stack_frame_variables": {"mode": "denylist", "terms": ["nickname"]}
            }
        }
    )
    events = capture_events()

    def raise_with_locals():
        safe_value = "not sensitive"  # noqa: F841
        password = "hunter2"  # noqa: F841
        nickname = "Bugsy"  # noqa: F841
        raise ValueError("boom")

    try:
        raise_with_locals()
    except ValueError:
        sentry_sdk.capture_exception()

    (event,) = events
    frame = event["exception"]["values"][0]["stacktrace"]["frames"][-1]

    assert frame["vars"]["safe_value"] == "'not sensitive'"
    assert frame["vars"]["password"] == "'[Filtered]'"
    assert frame["vars"]["nickname"] == "'[Filtered]'"


def test_serialize_frame_variables_serializer_failure(sentry_init):
    sentry_init(
        _experiments={
            "data_collection": {
                "stack_frame_variables": {"mode": "denylist", "terms": ["password"]}
            }
        }
    )

    failure_message = "<failed to serialize, use init(debug=True) to see error logs>"

    frame = sys._getframe()
    with mock.patch("sentry_sdk.serializer.serialize", return_value=failure_message):
        result = serialize_frame(frame)

    assert result["vars"] == failure_message


@pytest.mark.parametrize(
    "item,regex_list,expected_result",
    [
        ["", [], False],
        ["", None, False],
        ["some-string", ["some-string"], True],
        ["some-string", ["some"], False],
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


@pytest.mark.parametrize(
    "version,min_version,expected_pass",
    [
        ("1.0.0", (1, 0, 0), True),
        ("1.0.1", (2, 0, 0), False),
        ("1", (1, 0, 2), False),
        ("1.0", (1, 0, 2), False),
        ("1.0.1", (1, 0, 2), False),
        ("1.0.1", (1, 0, 1), True),
        ("1.0", (2,), False),
        (
            "1.0.1",
            (
                2,
                0,
            ),
            False,
        ),
        ("1.0.1", (2, 0, 0), False),
        ("2.0", (1,), True),
        (
            "2.0.1",
            (
                1,
                1,
            ),
            True,
        ),
        ("2.0.1", (1, 1, 2), True),
        ("1", (1, 0), True),
    ],
)
def test_check_minimum_version(monkeypatch, version, min_version, expected_pass):
    class TestIntegration(Integration):
        identifier = "test"

    monkeypatch.setattr(sentry_sdk.integrations, "_MIN_VERSIONS", {"test": min_version})
    try:
        _check_minimum_version(TestIntegration, parse_version(version))
    except DidNotEnable:
        if expected_pass:
            assert False, (
                "_check_minimum_version raised DidNotEnable when it shouldn't have"
            )
    else:
        if not expected_pass:
            assert False, (
                "_check_minimum_version didn't raise DidNotEnable when it was supposed to"
            )


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


def test_get_default_release_heroku_build_commit(monkeypatch):
    monkeypatch.setenv("HEROKU_BUILD_COMMIT", "heroku-build-commit-sha")

    with mock.patch("sentry_sdk.utils.get_git_revision", return_value=""):
        release = get_default_release()

    assert release == "heroku-build-commit-sha"


def test_get_default_release_heroku_slug_commit_fallback(monkeypatch):
    # Although deprecated by Heroku, HEROKU_SLUG_COMMIT should still be used if HEROKU_BUILD_COMMIT is not set
    monkeypatch.setenv("HEROKU_SLUG_COMMIT", "heroku-slug-commit-sha")

    with mock.patch("sentry_sdk.utils.get_git_revision", return_value=""):
        release = get_default_release()

    assert release == "heroku-slug-commit-sha"


def test_get_default_release_heroku_build_commit_takes_priority(monkeypatch):
    # HEROKU_BUILD_COMMIT should take priority over HEROKU_SLUG_COMMIT since it's the non-deprecated variable
    monkeypatch.setenv("HEROKU_BUILD_COMMIT", "heroku-build-commit-sha")
    monkeypatch.setenv("HEROKU_SLUG_COMMIT", "heroku-slug-commit-sha")

    with mock.patch("sentry_sdk.utils.get_git_revision", return_value=""):
        release = get_default_release()

    assert release == "heroku-build-commit-sha"


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


def test_get_current_thread_meta_failed_to_get_main_thread():
    results = Queue(maxsize=1)

    def target():
        with mock.patch("threading.current_thread", return_value="fake thread"):
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


@pytest.mark.parametrize(
    "options,expected_context_lines",
    [
        pytest.param({}, 5, id="no_data_collection-defaults_to_5"),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            5,
            id="data_collection-spec_default_5",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"frame_context_lines": 3}}},
            3,
            id="data_collection-frame_context_lines_3",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"frame_context_lines": 0}}},
            0,
            id="data_collection-frame_context_lines_0",
        ),
        pytest.param(
            {
                "_experiments": {"data_collection": {}},
                "include_source_context": False,
            },
            5,
            id="data_collection-spec_default_overrides_include_source_context_false",
        ),
    ],
)
def test_get_lines_from_file_frame_context_lines(
    sentry_init, options, expected_context_lines
):
    source = ["line{}\n".format(i) for i in range(20)]

    sentry_init(**options)

    def fake_getlines(filename):
        return source

    with mock.patch("sentry_sdk.utils.linecache.getlines", fake_getlines):
        pre_context, context_line, post_context = get_lines_from_file("filename", 10)

    assert context_line == "line10"
    assert len(pre_context) == expected_context_lines
    assert len(post_context) == expected_context_lines


def test_safe_serialize_plain_string():
    assert safe_serialize("already a string") == "already a string"


def test_safe_serialize_json_string():
    assert safe_serialize('{"key": "value"}') == '{"key": "value"}'


def test_safe_serialize_dict():
    assert safe_serialize({"key": "value"}) == '{"key": "value"}'


def test_safe_serialize_callable():
    def my_func():
        pass

    result = safe_serialize(my_func)
    assert result.startswith("<function")
    assert '"' not in result[:1]  # no wrapping quotes from json.dumps


def test_safe_serialize_object():
    class MyClass:
        def __init__(self):
            self.x = 1

    result = safe_serialize(MyClass())
    assert result.startswith("<MyClass")
    assert '"' not in result[:1]  # no wrapping quotes from json.dumps


def test_package_version_is_none():
    assert package_version("non_existent_package") is None
