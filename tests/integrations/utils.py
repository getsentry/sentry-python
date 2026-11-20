import json
import subprocess
import sys
from textwrap import dedent

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
        {"data_collection": {"user_info": True}},
        True,
        id="data_collection_user_info_true",
    ),
    pytest.param(
        {"data_collection": {"user_info": False}},
        False,
        id="data_collection_user_info_false",
    ),
    pytest.param(
        {
            "send_default_pii": True,
            "data_collection": {"user_info": False},
        },
        False,
        id="data_collection_wins_over_send_default_pii_true",
    ),
    pytest.param(
        {
            "send_default_pii": False,
            "data_collection": {"user_info": True},
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
    pytest.param(
        {
            "send_default_pii": True,
            "data_collection": {"user_info": False},
        },
        False,
        id="data_collection_wins_over_send_default_pii",
    ),
]

# Shared parametrization test matrix exercising the interaction between the
# ``data_collection.queues`` setting and the legacy ``send_default_pii`` boolean
# for job/task args and kwargs collected by queue integrations (rq, arq, huey).
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
    pytest.param(
        {"send_default_pii": False},
        SENSITIVE_DATA_SUBSTITUTE,
        SENSITIVE_DATA_SUBSTITUTE,
        id="no_pii",
    ),
    pytest.param(
        {
            "data_collection": {"queues": False},
            "send_default_pii": False,
        },
        None,
        None,
        id="data_collection_queues_off_with_no_pii",
    ),
    pytest.param(
        {
            "data_collection": {"queues": True},
            "send_default_pii": False,
        },
        [1],
        {"b": 0},
        id="data_collection_queues_on_with_no_pii",
    ),
]


def run_deferred_setup_app(tmp_path, identifier, trigger, app_imports, patched):
    """
    Runs an app in a fresh interpreter that calls ``init()`` with deferred setup of
    auto-enabling integrations on, and only afterwards runs ``app_imports``.
    """
    app = tmp_path / "app.py"
    app.write_text(
        dedent(
            """
            import json
            import os
            import sys

            import sentry_sdk

            events = []

            def before_send(event, hint):
                events.append(event)
                return None

            sentry_sdk.init(
                before_send=before_send,
                _experiments={{"deferred_auto_enabling_integrations": True}},
            )
            imported_during_init = {trigger!r} in sys.modules

            {app_imports}

            sentry_sdk.capture_message("hi")

            print(json.dumps({{
                "imported_during_init": imported_during_init,
                "integration_enabled": sentry_sdk.get_client().get_integration({identifier!r}) is not None,
                "patched": os.path.join("sentry_sdk", "integrations") in {patched}.__code__.co_filename,
                "reported_in_event": {identifier!r} in events[0]["sdk"]["integrations"],
            }}))
            """
        ).format(
            identifier=identifier,
            trigger=trigger,
            app_imports=app_imports,
            patched=patched,
        )
    )

    return json.loads(subprocess.check_output([sys.executable, str(app)]))
