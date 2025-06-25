from unittest.mock import MagicMock, patch

import pytest
from loguru import logger
from loguru._recattrs import RecordFile, RecordLevel

import sentry_sdk
from sentry_sdk.consts import VERSION
from sentry_sdk.integrations.loguru import LoguruIntegration, LoggingLevels
from tests.test_logs import envelopes_to_logs

logger.remove(0)  # don't print to console


@pytest.mark.parametrize(
    "level,created_event,expected_sentry_level",
    [
        # None - no breadcrumb
        # False - no event
        # True - event created
        (LoggingLevels.TRACE, None, "debug"),
        (LoggingLevels.DEBUG, None, "debug"),
        (LoggingLevels.INFO, False, "info"),
        (LoggingLevels.SUCCESS, False, "info"),
        (LoggingLevels.WARNING, False, "warning"),
        (LoggingLevels.ERROR, True, "error"),
        (LoggingLevels.CRITICAL, True, "critical"),
    ],
)
@pytest.mark.parametrize("disable_breadcrumbs", [True, False])
@pytest.mark.parametrize("disable_events", [True, False])
def test_just_log(
    sentry_init,
    capture_events,
    level,
    created_event,
    expected_sentry_level,
    disable_breadcrumbs,
    disable_events,
    uninstall_integration,
    request,
):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

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
        + "| tests.integrations.loguru.test_loguru:test_just_log:57 - test"
    )

    if not created_event:
        assert not events

        breadcrumbs = sentry_sdk.get_isolation_scope()._breadcrumbs
        if (
            not disable_breadcrumbs and created_event is not None
        ):  # not None == not TRACE or DEBUG level
            (breadcrumb,) = breadcrumbs
            assert breadcrumb["level"] == expected_sentry_level
            assert breadcrumb["category"] == "tests.integrations.loguru.test_loguru"
            assert breadcrumb["message"][23:] == formatted_message
        else:
            assert not breadcrumbs

        return

    if disable_events:
        assert not events
        return

    (event,) = events
    assert event["level"] == expected_sentry_level
    assert event["logger"] == "tests.integrations.loguru.test_loguru"
    assert event["logentry"]["message"][23:] == formatted_message


def test_breadcrumb_format(sentry_init, capture_events, uninstall_integration, request):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

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

    breadcrumbs = sentry_sdk.get_isolation_scope()._breadcrumbs
    (breadcrumb,) = breadcrumbs
    assert breadcrumb["message"] == formatted_message


def test_event_format(sentry_init, capture_events, uninstall_integration, request):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

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


def test_sentry_logs_warning(
    sentry_init, capture_envelopes, uninstall_integration, request
):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    logger.warning("this is {} a {}", "just", "template")

    sentry_sdk.get_client().flush()
    logs = envelopes_to_logs(envelopes)

    attrs = logs[0]["attributes"]
    assert "code.file.path" in attrs
    assert "code.line.number" in attrs
    assert attrs["logger.name"] == "tests.integrations.loguru.test_loguru"
    assert attrs["sentry.environment"] == "production"
    assert attrs["sentry.origin"] == "auto.logger.loguru"
    assert logs[0]["severity_number"] == 13
    assert logs[0]["severity_text"] == "warn"


def test_sentry_logs_debug(
    sentry_init, capture_envelopes, uninstall_integration, request
):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    logger.debug("this is %s a template %s", "1", "2")
    sentry_sdk.get_client().flush()

    assert len(envelopes) == 0


def test_sentry_log_levels(
    sentry_init, capture_envelopes, uninstall_integration, request
):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(
        integrations=[LoguruIntegration(sentry_logs_level=LoggingLevels.SUCCESS)],
        _experiments={"enable_logs": True},
    )
    envelopes = capture_envelopes()

    logger.trace("this is a log")
    logger.debug("this is a log")
    logger.info("this is a log")
    logger.success("this is a log")
    logger.warning("this is a log")
    logger.error("this is a log")
    logger.critical("this is a log")

    sentry_sdk.get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert len(logs) == 4

    assert logs[0]["severity_number"] == 11
    assert logs[0]["severity_text"] == "info"
    assert logs[1]["severity_number"] == 13
    assert logs[1]["severity_text"] == "warn"
    assert logs[2]["severity_number"] == 17
    assert logs[2]["severity_text"] == "error"
    assert logs[3]["severity_number"] == 21
    assert logs[3]["severity_text"] == "fatal"


def test_disable_loguru_logs(
    sentry_init, capture_envelopes, uninstall_integration, request
):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(
        integrations=[LoguruIntegration(sentry_logs_level=None)],
        _experiments={"enable_logs": True},
    )
    envelopes = capture_envelopes()

    logger.trace("this is a log")
    logger.debug("this is a log")
    logger.info("this is a log")
    logger.success("this is a log")
    logger.warning("this is a log")
    logger.error("this is a log")
    logger.critical("this is a log")

    sentry_sdk.get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert len(logs) == 0


def test_disable_sentry_logs(
    sentry_init, capture_envelopes, uninstall_integration, request
):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(
        _experiments={"enable_logs": False},
    )
    envelopes = capture_envelopes()

    logger.trace("this is a log")
    logger.debug("this is a log")
    logger.info("this is a log")
    logger.success("this is a log")
    logger.warning("this is a log")
    logger.error("this is a log")
    logger.critical("this is a log")

    sentry_sdk.get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert len(logs) == 0


def test_no_log_infinite_loop(
    sentry_init, capture_envelopes, uninstall_integration, request
):
    """
    In debug mode, there should be no infinite loops even when a low log level is set.
    """
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(
        _experiments={"enable_logs": True},
        integrations=[LoguruIntegration(sentry_logs_level=LoggingLevels.DEBUG)],
        debug=True,
    )
    envelopes = capture_envelopes()

    logger.debug("this is %s a template %s", "1", "2")
    sentry_sdk.get_client().flush()

    assert len(envelopes) == 1


def test_logging_errors(sentry_init, capture_envelopes, uninstall_integration, request):
    """We're able to log errors without erroring."""
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    logger.error(Exception("test exc 1"))
    logger.error("error is %s", Exception("test exc 2"))
    sentry_sdk.get_client().flush()

    error_event_1 = envelopes[0].items[0].payload.json
    assert error_event_1["level"] == "error"
    error_event_2 = envelopes[1].items[0].payload.json
    assert error_event_2["level"] == "error"

    logs = envelopes_to_logs(envelopes)
    assert logs[0]["severity_text"] == "error"
    assert "code.line.number" in logs[0]["attributes"]

    assert logs[1]["severity_text"] == "error"
    assert "code.line.number" in logs[1]["attributes"]

    assert len(logs) == 2


def test_log_strips_project_root(
    sentry_init, capture_envelopes, uninstall_integration, request
):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(
        _experiments={"enable_logs": True},
        project_root="/custom/test",
    )
    envelopes = capture_envelopes()

    class FakeMessage:
        def __init__(self, *args, **kwargs):
            pass

        @property
        def record(self):
            return {
                "elapsed": MagicMock(),
                "exception": None,
                "file": RecordFile(name="app.py", path="/custom/test/blah/path.py"),
                "function": "<module>",
                "level": RecordLevel(name="ERROR", no=20, icon=""),
                "line": 35,
                "message": "some message",
                "module": "app",
                "name": "__main__",
                "process": MagicMock(),
                "thread": MagicMock(),
                "time": MagicMock(),
                "extra": MagicMock(),
            }

        @record.setter
        def record(self, val):
            pass

    with patch("loguru._handler.Message", FakeMessage):
        logger.error("some message")

    sentry_sdk.get_client().flush()

    logs = envelopes_to_logs(envelopes)
    assert len(logs) == 1
    attrs = logs[0]["attributes"]
    assert attrs["code.file.path"] == "blah/path.py"


def test_log_keeps_full_path_if_not_in_project_root(
    sentry_init, capture_envelopes, uninstall_integration, request
):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(
        _experiments={"enable_logs": True},
        project_root="/custom/test",
    )
    envelopes = capture_envelopes()

    class FakeMessage:
        def __init__(self, *args, **kwargs):
            pass

        @property
        def record(self):
            return {
                "elapsed": MagicMock(),
                "exception": None,
                "file": RecordFile(name="app.py", path="/blah/path.py"),
                "function": "<module>",
                "level": RecordLevel(name="ERROR", no=20, icon=""),
                "line": 35,
                "message": "some message",
                "module": "app",
                "name": "__main__",
                "process": MagicMock(),
                "thread": MagicMock(),
                "time": MagicMock(),
                "extra": MagicMock(),
            }

        @record.setter
        def record(self, val):
            pass

    with patch("loguru._handler.Message", FakeMessage):
        logger.error("some message")

    sentry_sdk.get_client().flush()

    logs = envelopes_to_logs(envelopes)
    assert len(logs) == 1
    attrs = logs[0]["attributes"]
    assert attrs["code.file.path"] == "/blah/path.py"


def test_logger_with_all_attributes(
    sentry_init, capture_envelopes, uninstall_integration, request
):
    uninstall_integration("loguru")
    request.addfinalizer(logger.remove)

    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    logger.warning("log #{}", 1)
    sentry_sdk.get_client().flush()

    logs = envelopes_to_logs(envelopes)

    attributes = logs[0]["attributes"]

    assert "process.pid" in attributes
    assert isinstance(attributes["process.pid"], int)
    del attributes["process.pid"]

    assert "sentry.release" in attributes
    assert isinstance(attributes["sentry.release"], str)
    del attributes["sentry.release"]

    assert "server.address" in attributes
    assert isinstance(attributes["server.address"], str)
    del attributes["server.address"]

    assert "thread.id" in attributes
    assert isinstance(attributes["thread.id"], int)
    del attributes["thread.id"]

    assert "code.file.path" in attributes
    assert isinstance(attributes["code.file.path"], str)
    del attributes["code.file.path"]

    assert "code.function.name" in attributes
    assert isinstance(attributes["code.function.name"], str)
    del attributes["code.function.name"]

    assert "code.line.number" in attributes
    assert isinstance(attributes["code.line.number"], int)
    del attributes["code.line.number"]

    assert "process.executable.name" in attributes
    assert isinstance(attributes["process.executable.name"], str)
    del attributes["process.executable.name"]

    assert "thread.name" in attributes
    assert isinstance(attributes["thread.name"], str)
    del attributes["thread.name"]

    assert attributes.pop("sentry.sdk.name").startswith("sentry.python")

    # Assert on the remaining non-dynamic attributes.
    assert attributes == {
        "logger.name": "tests.integrations.loguru.test_loguru",
        "sentry.origin": "auto.logger.loguru",
        "sentry.environment": "production",
        "sentry.sdk.version": VERSION,
        "sentry.severity_number": 13,
        "sentry.severity_text": "warn",
    }
