import pytest


# Shared parametrization test matrix for ``data_collection.user_info`` setting.
# Each case is ``(init_kwargs, expect_user_info)`` where the second element indicates
# whether user info (IP address, user identity, etc.) is expected to be collected.
DATA_COLLECTION_USER_INFO_CASES = [
    pytest.param(
        {"data_collection": {"user_info": True}},
        True,
        id="data_collection_user_info_true",
    ),
    pytest.param(
        {"data_collection": {"user_info": False}},
        False,
        id="data_collection_user_info_false",
    ),
]

# Shared parametrization test matrix for ``REMOTE_ADDR`` on events.
# Each case is ``(init_kwargs, expect_remote_addr)``.
DATA_COLLECTION_REMOTE_ADDR_CASES = [
    pytest.param({"data_collection": {}}, True, id="data_collection_default"),
    pytest.param(
        {"data_collection": {"user_info": True}},
        True,
        id="data_collection_user_info_true",
    ),
    pytest.param(
        {"data_collection": {"user_info": False}},
        False,
        id="data_collection_user_info_false",
    ),
]


# Shared parametrization test matrix for ``data_collection.queues`` setting.
# Each case is ``(init_kwargs, expected_args, expected_kwargs)`` where ``None`` for
# the expected values means args/kwargs are not collected at all.
DATA_COLLECTION_QUEUES_CASES = [
    pytest.param(
        {"data_collection": {}},
        [1],
        {"b": 0},
        id="data_collection_default",
    ),
    pytest.param(
        {"data_collection": {"queues": True}},
        [1],
        {"b": 0},
        id="data_collection_queues_on",
    ),
    pytest.param(
        {"data_collection": {"queues": False}},
        None,
        None,
        id="data_collection_queues_off",
    ),
]
