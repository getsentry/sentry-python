import sentry_sdk

from sentry_sdk import Hub
from sentry_sdk.sessions import auto_session_tracking

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def sorted_aggregates(item):
    aggregates = item["aggregates"]
    aggregates.sort(key=lambda item: (item["started"], item.get("did", "")))
    return aggregates


def test_basic(sentry_init, capture_envelopes):
    sentry_init(release="fun-release", environment="not-fun-env")
    envelopes = capture_envelopes()

    hub = Hub.current
    hub.start_session()

    try:
        with hub.configure_scope() as scope:
            scope.set_user({"id": "42"})
            raise Exception("all is wrong")
    except Exception:
        hub.capture_exception()
    hub.end_session()
    hub.flush()

    assert len(envelopes) == 2
    assert envelopes[0].get_event() is not None

    sess = envelopes[1]
    assert len(sess.items) == 1
    sess_event = sess.items[0].payload.json

    assert sess_event["attrs"] == {
        "release": "fun-release",
        "environment": "not-fun-env",
    }
    assert sess_event["did"] == "42"
    assert sess_event["init"]
    assert sess_event["status"] == "exited"
    assert sess_event["errors"] == 1


def test_aggregates(sentry_init, capture_envelopes):
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
    )
    envelopes = capture_envelopes()

    hub = Hub.current

    with auto_session_tracking(session_mode="request"):
        with sentry_sdk.push_scope():
            try:
                with sentry_sdk.configure_scope() as scope:
                    scope.set_user({"id": "42"})
                    raise Exception("all is wrong")
            except Exception:
                sentry_sdk.capture_exception()

    with auto_session_tracking(session_mode="request"):
        pass

    hub.start_session(session_mode="request")
    hub.end_session()

    sentry_sdk.flush()

    assert len(envelopes) == 2
    assert envelopes[0].get_event() is not None

    sess = envelopes[1]
    assert len(sess.items) == 1
    sess_event = sess.items[0].payload.json
    assert sess_event["attrs"] == {
        "release": "fun-release",
        "environment": "not-fun-env",
    }

    aggregates = sorted_aggregates(sess_event)
    assert len(aggregates) == 1
    assert aggregates[0]["exited"] == 2
    assert aggregates[0]["errored"] == 1


def test_aggregates_explicitly_disabled_session_tracking_request_mode(
    sentry_init, capture_envelopes
):
    sentry_init(
        release="fun-release", environment="not-fun-env", auto_session_tracking=False
    )
    envelopes = capture_envelopes()

    hub = Hub.current

    with auto_session_tracking(session_mode="request"):
        with sentry_sdk.push_scope():
            try:
                raise Exception("all is wrong")
            except Exception:
                sentry_sdk.capture_exception()

    with auto_session_tracking(session_mode="request"):
        pass

    hub.start_session(session_mode="request")
    hub.end_session()

    sentry_sdk.flush()

    sess = envelopes[1]
    assert len(sess.items) == 1
    sess_event = sess.items[0].payload.json

    aggregates = sorted_aggregates(sess_event)
    assert len(aggregates) == 1
    assert aggregates[0]["exited"] == 1
    assert "errored" not in aggregates[0]


def test_no_thread_on_shutdown_no_errors(sentry_init):
    sentry_init(
        release="fun-release",
        environment="not-fun-env",
    )

    hub = Hub.current

    # make it seem like the interpreter is shutting down
    with mock.patch(
        "threading.Thread.start",
        side_effect=RuntimeError("can't create new thread at interpreter shutdown"),
    ):
        with auto_session_tracking(session_mode="request"):
            with sentry_sdk.push_scope():
                try:
                    raise Exception("all is wrong")
                except Exception:
                    sentry_sdk.capture_exception()

        with auto_session_tracking(session_mode="request"):
            pass

        hub.start_session(session_mode="request")
        hub.end_session()

        sentry_sdk.flush()
