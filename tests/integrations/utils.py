import pytest

from sentry_sdk.utils import SENSITIVE_DATA_SUBSTITUTE

# Shared parametrization test matrix exercising the precedence between the legacy
# ``send_default_pii`` boolean and the ``data_collection.user_info`` setting.
# Each case is ``(init_kwargs, expect_user_info)`` where the second element indicates
# whether user info (IP address, user identity, etc.) is expected to be collected.
DATA_COLLECTION_USER_INFO_CASES = [
    pytest.param({"send_default_pii": True}, True, id="legacy_send_default_pii_true"),
    pytest.param(
        {"send_default_pii": False}, False, id="legacy_send_default_pii_false"
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"user_info": True}}},
        True,
        id="data_collection_user_info_true",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"user_info": False}}},
        False,
        id="data_collection_user_info_false",
    ),
    pytest.param(
        {
            "send_default_pii": True,
            "_experiments": {"data_collection": {"user_info": False}},
        },
        False,
        id="data_collection_wins_over_send_default_pii_true",
    ),
    pytest.param(
        {
            "send_default_pii": False,
            "_experiments": {"data_collection": {"user_info": True}},
        },
        True,
        id="data_collection_wins_over_send_default_pii_false",
    ),
]

# Shared parametrization test matrix for ``REMOTE_ADDR`` on events in
# integrations that set it unconditionally pre-data collection (tornado, sanic,
# aiohttp). Each case is ``(init_kwargs, expect_remote_addr)``: the address is
# only gated once ``data_collection`` is enabled, so the legacy
# ``send_default_pii`` cases still expect it to be collected.
DATA_COLLECTION_REMOTE_ADDR_CASES = [
    pytest.param({}, True, id="defaults"),
    pytest.param({"send_default_pii": True}, True, id="send_default_pii_true"),
    pytest.param({"send_default_pii": False}, True, id="send_default_pii_false"),
    pytest.param(
        {"_experiments": {"data_collection": {}}}, True, id="data_collection_default"
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"user_info": True}}},
        True,
        id="data_collection_user_info_true",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"user_info": False}}},
        False,
        id="data_collection_user_info_false",
    ),
    pytest.param(
        {
            "send_default_pii": True,
            "_experiments": {"data_collection": {"user_info": False}},
        },
        False,
        id="data_collection_wins_over_send_default_pii",
    ),
]

# Shared parametrization test matrix exercising the interaction between the
# ``data_collection.queues`` experiment and the legacy ``send_default_pii`` boolean
# for job/task args and kwargs collected by queue integrations (rq, arq, huey).
# Each case is ``(init_kwargs, expected_args, expected_kwargs)`` where ``None`` for
# the expected values means args/kwargs are not collected at all.
DATA_COLLECTION_QUEUES_CASES = [
    pytest.param(
        {"_experiments": {"data_collection": {}}},
        [1],
        {"b": 0},
        id="data_collection_default",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"queues": True}}},
        [1],
        {"b": 0},
        id="data_collection_queues_on",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"queues": False}}},
        None,
        None,
        id="data_collection_queues_off",
    ),
    pytest.param(
        {"send_default_pii": False},
        SENSITIVE_DATA_SUBSTITUTE,
        SENSITIVE_DATA_SUBSTITUTE,
        id="no_pii",
    ),
    pytest.param(
        {
            "_experiments": {"data_collection": {"queues": False}},
            "send_default_pii": False,
        },
        None,
        None,
        id="data_collection_queues_off_with_no_pii",
    ),
    pytest.param(
        {
            "_experiments": {"data_collection": {"queues": True}},
            "send_default_pii": False,
        },
        [1],
        {"b": 0},
        id="data_collection_queues_on_with_no_pii",
    ),
]
