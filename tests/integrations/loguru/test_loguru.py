import pytest
from loguru import logger

import sentry_sdk
from sentry_sdk.integrations.loguru import LoguruIntegration, LoggingLevels

logger.remove(0)  # don't print to console


@pytest.mark.parametrize(
    "level,created_event",
    [
        # None - no breadcrumb
        # False - no event
        # True - event created
        (LoggingLevels.TRACE, None),
        (LoggingLevels.DEBUG, None),
        (LoggingLevels.INFO, False),
        (LoggingLevels.SUCCESS, False),
        (LoggingLevels.WARNING, False),
        (LoggingLevels.ERROR, True),
        (LoggingLevels.CRITICAL, True),
    ],
)
@pytest.mark.parametrize("disable_breadcrumbs", [True, False])
@pytest.mark.parametrize("disable_events", [True, False])
def test_just_log(
    sentry_init,
    capture_events,
    level,
    created_event,
    disable_breadcrumbs,
    disable_events,
):
    sentry_init(
        integrations=[
            LoguruIntegration(
                level=None if disable_breadcrumbs else LoggingLevels.INFO.value,
                event_level=None if disable_events else LoggingLevels.ERROR.value,
            )
        ],
        default_integrations=False,
    )
    events = capture_events()

    getattr(logger, level.name.lower())("test")

    formatted_message = (
        " | "
        + "{:9}".format(level.name.upper())
        + "| tests.integrations.loguru.test_loguru:test_just_log:46 - test"
    )

    if not created_event:
        assert not events

        breadcrumbs = sentry_sdk.Hub.current.scope._breadcrumbs
        if (
            not disable_breadcrumbs and created_event is not None
        ):  # not None == not TRACE or DEBUG level
            (breadcrumb,) = breadcrumbs
            assert breadcrumb["level"] == level.name.lower()
            assert breadcrumb["category"] == "tests.integrations.loguru.test_loguru"
            assert breadcrumb["message"][23:] == formatted_message
        else:
            assert not breadcrumbs

        return

    if disable_events:
        assert not events
        return

    (event,) = events
    assert event["level"] == (level.name.lower())
    assert event["logger"] == "tests.integrations.loguru.test_loguru"
    assert event["logentry"]["message"][23:] == formatted_message


def test_breadcrumb_format(sentry_init, capture_events):
    sentry_init(
        integrations=[
            LoguruIntegration(
                level=LoggingLevels.INFO.value,
                event_level=None,
                breadcrumb_format="{message}",
            )
        ],
        default_integrations=False,
    )

    logger.info("test")
    formatted_message = "test"

    breadcrumbs = sentry_sdk.Hub.current.scope._breadcrumbs
    (breadcrumb,) = breadcrumbs
    assert breadcrumb["message"] == formatted_message


def test_event_format(sentry_init, capture_events):
    sentry_init(
        integrations=[
            LoguruIntegration(
                level=None,
                event_level=LoggingLevels.ERROR.value,
                event_format="{message}",
            )
        ],
        default_integrations=False,
    )
    events = capture_events()

    logger.error("test")
    formatted_message = "test"

    (event,) = events
    assert event["logentry"]["message"] == formatted_message
