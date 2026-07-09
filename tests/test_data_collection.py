import warnings

import pytest

import sentry_sdk
from sentry_sdk.data_collection import _ALL_HTTP_BODY_TYPES
from sentry_sdk.utils import has_data_collection_enabled


def test_kvcb_invalid_mode():
    with pytest.raises(ValueError):
        sentry_sdk.init(_experiments={"data_collection": {"cookies": {"mode": "nope"}}})  # type: ignore Purposely ignoring to test invalid option


def test_kvcb_from_dict_defaults_mode():
    sentry_sdk.init(
        _experiments={
            "data_collection": {"cookies": {"mode": "denylist", "terms": ["x"]}}
        }
    )
    client = sentry_sdk.get_client()
    assert client.options["data_collection"]["cookies"] == {
        "mode": "denylist",
        "terms": ["x"],
    }


def test_http_headers_collection_defaults():
    default_terms = ["forwarded", "-ip", "remote-", "via", "-user"]

    sentry_sdk.init(_experiments={"data_collection": {"http_headers": {}}})  # type: ignore Purposely ignoring to test invalid option
    client = sentry_sdk.get_client()
    assert client.options["data_collection"]["http_headers"]["request"] == {
        "mode": "denylist"
    }

    sentry_sdk.init(_experiments={"data_collection": {"http_headers": "off"}})  # type: ignore Purposely ignoring to test invalid option
    client = sentry_sdk.get_client()
    assert client.options["data_collection"]["http_headers"]["request"] == {
        "mode": "denylist"
    }

    sentry_sdk.init()
    client = sentry_sdk.get_client()
    assert client.options["data_collection"]["http_headers"]["request"] == {
        "mode": "denylist",
        "terms": default_terms,
    }


def test_http_headers_use_default_in_setting_with_missing_config():
    sentry_sdk.init(
        _experiments={
            "data_collection": {
                "http_headers": {
                    "request": {"mode": "allowlist", "terms": ["x-id"]},
                }
            }
        }
    )

    client = sentry_sdk.get_client()

    assert client.options["data_collection"]["http_headers"]["request"] == {
        "mode": "allowlist",
        "terms": ["x-id"],
    }


def _initialize_client_with_config(**options):
    sentry_sdk.init(**options)
    return sentry_sdk.get_client().options["data_collection"]


def _get(dc, path):
    obj = dc
    for part in path.split("."):
        obj = obj[part]
    return obj


@pytest.mark.parametrize(
    "options,expected",
    [
        pytest.param(
            {},
            {
                "user_info": False,
                "gen_ai.inputs": False,
                "gen_ai.outputs": False,
                "cookies.mode": "off",
                "query_params.mode": "off",
                "http_headers.request.mode": "denylist",
                "http_bodies": _ALL_HTTP_BODY_TYPES,
                "queues": False,
                "frame_context_lines": 5,
            },
            id="no_options_collects_no_pii",
        ),
        pytest.param(
            {"send_default_pii": True},
            {
                "user_info": True,
                "gen_ai.inputs": True,
                "gen_ai.outputs": True,
                "cookies.mode": "denylist",
                "query_params.mode": "denylist",
                "queues": True,
            },
            id="send_default_pii_true_collects_pii",
        ),
        pytest.param(
            {"send_default_pii": False},
            {"user_info": False, "cookies.mode": "off", "queues": False},
            id="send_default_pii_false_collects_no_pii",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            {
                "user_info": True,
                "gen_ai.inputs": True,
                "gen_ai.outputs": True,
                "cookies.mode": "denylist",
                "query_params.mode": "denylist",
                "http_bodies": _ALL_HTTP_BODY_TYPES,
                "queues": True,
            },
            id="explicit_data_collection_uses_spec_defaults",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"user_info": False, "http_bodies": []}
                }
            },
            {
                "user_info": False,
                "http_bodies": [],
                "gen_ai.inputs": True,
                "cookies.mode": "denylist",
            },
            id="explicit_partial_fills_omitted_with_spec_defaults",
        ),
        pytest.param(
            {
                "_experiments": {"data_collection": {}},
                "include_local_variables": False,
                "include_source_context": False,
            },
            {"stack_frame_variables": False, "frame_context_lines": 0},
            id="explicit_frame_fields_fall_back_to_legacy_options",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "cookies": {"mode": "off"},
                        "query_params": {"mode": "allowlist", "terms": ["page"]},
                        "http_headers": {"request": {"mode": "off"}},
                        "gen_ai": {"inputs": False, "outputs": True},
                    }
                }
            },
            {
                "cookies.mode": "off",
                "query_params.mode": "allowlist",
                "query_params.terms": ["page"],
                "http_headers.request.mode": "off",
                "gen_ai.inputs": False,
                "gen_ai.outputs": True,
            },
            id="accepts_dict_with_nested_dicts",
        ),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {
                        "cookies": None,
                        "http_headers": None,
                        "query_params": None,
                        "graphql": None,
                        "gen_ai": None,
                    }
                }
            },
            {
                "cookies.mode": "denylist",
                "http_headers.request.mode": "denylist",
                "query_params.mode": "denylist",
                "graphql.document": True,
                "graphql.variables": True,
                "gen_ai.inputs": True,
                "gen_ai.outputs": True,
            },
            id="none_values_fall_back_to_spec_defaults",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {}}},
            {
                "graphql.document": True,
                "graphql.variables": True,
                "database_query_data": True,
            },
            id="explicit_graphql_database_defaults",
        ),
        pytest.param(
            {"send_default_pii": False},
            {
                "graphql.document": False,
                "graphql.variables": False,
                "database_query_data": False,
                "queues": False,
            },
            id="legacy_pii_off_gates_graphql_database_and_queues",
        ),
        pytest.param(
            {"send_default_pii": True},
            {
                "graphql.document": True,
                "graphql.variables": True,
                "database_query_data": True,
                "queues": True,
            },
            id="legacy_pii_on_collects_graphql_database_and_queues",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"graphql": {"variables": False}}}},
            {"graphql.document": True, "graphql.variables": False},
            id="explicit_partial_graphql_fills_omitted",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"frame_context_lines": True}}},
            {"frame_context_lines": 5},
            id="frame_context_lines_bool_fallback_true",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"frame_context_lines": False}}},
            {"frame_context_lines": 0},
            id="frame_context_lines_bool_fallback_false",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"frame_context_lines": 3}}},
            {"frame_context_lines": 3},
            id="frame_context_lines_bool_fallback_3",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"frame_context_lines": 0}}},
            {"frame_context_lines": 0},
            id="frame_context_lines_bool_fallback_0",
        ),
    ],
)
def test_initalize_client_data_collection(options, expected):
    dc = _initialize_client_with_config(**options)
    for path, value in expected.items():
        assert _get(dc, path) == value, f"{path} != {value!r}"


def test_initialize_client_data_collection_overrides_send_default_pii_and_warns():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dc = _initialize_client_with_config(
            send_default_pii=True,
            _experiments={"data_collection": {"user_info": False}},
        )
    assert dc["user_info"] is False  # data_collection wins
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


@pytest.mark.parametrize(
    "init_kwargs,expected",
    [
        pytest.param(
            {},
            {
                "user_info": False,
                "provided_by_user": False,
                "should_send_default_pii": False,
            },
            id="defaults_to_no_pii",
        ),
        pytest.param(
            {"send_default_pii": True},
            {"user_info": True, "provided_by_user": False},
            id="send_default_pii_enables_user_info",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"user_info": False}}},
            {"user_info": False, "provided_by_user": True},
            id="explicit_data_collection_overrides_user_info",
        ),
        pytest.param(
            {"spotlight": True},
            {"provided_by_user": False, "user_info": True},
            id="dsnless_spotlight_rederives_data_collection",
        ),
        pytest.param(
            {
                "spotlight": True,
                "_experiments": {"data_collection": {"user_info": False}},
            },
            {"provided_by_user": True, "user_info": False},
            id="dsnless_spotlight_respects_explicit_data_collection",
        ),
    ],
)
def test_client_data_collection_settings(init_kwargs, expected):
    sentry_sdk.init(**init_kwargs)
    client = sentry_sdk.get_client()
    for key, value in expected.items():
        if key == "should_send_default_pii":
            assert client.should_send_default_pii() is value
        else:
            assert client.options["data_collection"][key] is value


def test_has_data_collection_enabled_gates_on_presence():
    assert has_data_collection_enabled(None) is False
    assert has_data_collection_enabled({"_experiments": {}}) is False
    assert (
        has_data_collection_enabled({"_experiments": {"data_collection": {}}}) is True
    )


def test_no_experiments_falls_back_to_send_default_pii():
    sentry_sdk.init(send_default_pii=True)
    client = sentry_sdk.get_client()
    dc = client.options["data_collection"]
    assert dc["provided_by_user"] is False
    assert dc["user_info"] is True
    assert has_data_collection_enabled(client.options) is False
