from unittest import mock

import sentry_sdk
from sentry_sdk.sessions import track_session


def sorted_aggregates(item):
    aggregates = item["aggregates"]
    aggregates.sort(key=lambda item: (item["started"], item.get("did", "")))
    return aggregates


def test_basic(sentry_init, capture_envelopes):
    sentry_init(release="fun-release", environment="not-fun-env")
    envelopes = capture_envelopes()

    sentry_sdk.get_isolation_scope().start_session()

    try:
        scope = sentry_sdk.get_current_scope()
        scope.set_user({"id": "42"})
        raise Exception("all is wrong")
    except Exception:
        sentry_sdk.capture_exception()

    sentry_sdk.get_isolation_scope().end_session()
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

    with sentry_sdk.isolation_scope() as scope:
        with track_session(scope, session_mode="request"):
            try:
                scope.set_user({"id": "42"})
                raise Exception("all is wrong")
            except Exception:
                sentry_sdk.capture_exception()

    with sentry_sdk.isolation_scope() as scope:
        with track_session(scope, session_mode="request"):
            pass

    sentry_sdk.get_isolation_scope().start_session(session_mode="request")
    sentry_sdk.get_isolation_scope().end_session()
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
        release="fun-release",
        environment="not-fun-env",
        auto_session_tracking=False,
    )
    envelopes = capture_envelopes()

    with sentry_sdk.isolation_scope() as scope:
        with track_session(scope, session_mode="request"):
            try:
                raise Exception("all is wrong")
            except Exception:
                sentry_sdk.capture_exception()

    with sentry_sdk.isolation_scope() as scope:
        with track_session(scope, session_mode="request"):
            pass

    sentry_sdk.get_isolation_scope().start_session(session_mode="request")
    sentry_sdk.get_isolation_scope().end_session()
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

    # make it seem like the interpreter is shutting down
    with mock.patch(
        "threading.Thread.start",
        side_effect=RuntimeError("can't create new thread at interpreter shutdown"),
    ):
        with sentry_sdk.isolation_scope() as scope:
            with track_session(scope, session_mode="request"):
                try:
                    raise Exception("all is wrong")
                except Exception:
                    sentry_sdk.capture_exception()

        with sentry_sdk.isolation_scope() as scope:
            with track_session(scope, session_mode="request"):
                pass

        sentry_sdk.get_isolation_scope().start_session(session_mode="request")
        sentry_sdk.get_isolation_scope().end_session()
        sentry_sdk.flush()

    # If we reach this point without error, the test is successful.
