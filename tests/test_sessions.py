import time

from sentry_sdk import Hub


def test_basic(sentry_init, capture_envelopes):
    sentry_init(release="fun-release", environment="not-fun-env")
    envelopes = capture_envelopes()

    hub = Hub.current
    hub.start_session()

    try:
        with hub.configure_scope() as scope:
            scope.set_user({"id": 42})
            time.sleep(0.2)
            raise Exception("all is wrong")
    except Exception:
        hub.capture_exception()
    hub.flush()

    assert len(envelopes) == 2
    assert envelopes[0].get_event() is not None

    sess = envelopes[1]
    assert len(sess.items) == 1
    sess_event = sess.items[0].payload.json

    assert sess_event["did"] == "42"
    assert sess_event["status"] == "degraded"
    assert sess_event["duration"] > 0.1
    assert sess_event["duration"] < 1.0
    assert sess_event["attrs"] == {
        "release": "fun-release",
        "environment": "not-fun-env",
    }
