import sentry_sdk

from sentry_sdk import Hub
from sentry_sdk.sessions import auto_session_tracking

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
    # TODO: The SDK needs a setting to explicitly choose between application-mode
    # or request-mode sessions.
    # assert sess_event["did"] == "42"
    # assert sess_event["init"]
    # assert sess_event["status"] == "exited"
    # assert sess_event["errors"] == 1


def test_aggregates(sentry_init, capture_envelopes):
    sentry_init(release="fun-release", environment="not-fun-env",
        _experiments=dict(
            auto_session_tracking=True,
        ),)
    envelopes = capture_envelopes()

    hub = Hub.current

    with auto_session_tracking():
        with sentry_sdk.push_scope():
            try:
                with sentry_sdk.configure_scope() as scope:
                    scope.set_user({"id": "42"})
                    raise Exception("all is wrong")
            except Exception:
                sentry_sdk.capture_exception()
    
    with auto_session_tracking():
        pass
    
    hub.start_session()
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
    assert len(aggregates) == 2
    assert aggregates[0]["exited"] == 2
    assert aggregates[1]["did"] == "42"
    assert aggregates[1]["errored"] == 1
