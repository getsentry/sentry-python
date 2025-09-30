import sys

import pytest

from sentry_sdk.integrations.sys_exit import SysExitIntegration


@pytest.mark.parametrize(
    ("integration_params", "exit_status", "should_capture"),
    (
        ({}, 0, False),
        ({}, 1, True),
        ({}, None, False),
        ({}, "unsuccessful exit", True),
        ({"capture_successful_exits": False}, 0, False),
        ({"capture_successful_exits": False}, 1, True),
        ({"capture_successful_exits": False}, None, False),
        ({"capture_successful_exits": False}, "unsuccessful exit", True),
        ({"capture_successful_exits": True}, 0, True),
        ({"capture_successful_exits": True}, 1, True),
        ({"capture_successful_exits": True}, None, True),
        ({"capture_successful_exits": True}, "unsuccessful exit", True),
    ),
)
def test_sys_exit(
    sentry_init, capture_events, integration_params, exit_status, should_capture
):
    sentry_init(integrations=[SysExitIntegration(**integration_params)])

    events = capture_events()

    # Manually catch the sys.exit rather than using pytest.raises because IDE does not recognize that pytest.raises
    # will catch SystemExit.
    try:
        sys.exit(exit_status)
    except SystemExit:
        ...
    else:
        pytest.fail("Patched sys.exit did not raise SystemExit")

    if should_capture:
        (event,) = events
        (exception_value,) = event["exception"]["values"]

        assert exception_value["type"] == "SystemExit"
        assert exception_value["value"] == (
            str(exit_status) if exit_status is not None else ""
        )
    else:
        assert len(events) == 0


def test_sys_exit_integration_not_auto_enabled(sentry_init, capture_events):
    sentry_init()  # No SysExitIntegration

    events = capture_events()

    # Manually catch the sys.exit rather than using pytest.raises because IDE does not recognize that pytest.raises
    # will catch SystemExit.
    try:
        sys.exit(1)
    except SystemExit:
        ...
    else:
        pytest.fail(
            "sys.exit should not be patched, but it must have been because it did not raise SystemExit"
        )

    assert len(events) == 0, (
        "No events should have been captured because sys.exit should not have been patched"
    )
