import sentry_sdk
import pytest
from sentry_sdk import start_session, end_session, capture_exception
from sentry_sdk.sessions import track_session


def test_start_session_basic(sentry_init, capture_envelopes):
    """Test that start_session starts a session on the isolation scope."""
    sentry_init(release="test-release", environment="test-env")
    envelopes = capture_envelopes()

    # Start a session using the top-level API
    start_session()
    
    # End the session
    end_session()
    sentry_sdk.flush()

    # Check that we got a session envelope
    assert len(envelopes) == 1
    sess = envelopes[0]
    assert len(sess.items) == 1
    sess_event = sess.items[0].payload.json

    assert sess_event["attrs"] == {
        "release": "test-release",
        "environment": "test-env",
    }
    assert sess_event["init"]
    assert sess_event["status"] == "exited"


def test_start_session_with_mode(sentry_init, capture_envelopes):
    """Test that start_session accepts session_mode parameter."""
    sentry_init(release="test-release", environment="test-env")
    envelopes = capture_envelopes()

    # Start a session with request mode
    start_session(session_mode="request")
    end_session()
    sentry_sdk.flush()

    # Request mode sessions are aggregated
    assert len(envelopes) == 1
    sess = envelopes[0]
    assert len(sess.items) == 1
    sess_event = sess.items[0].payload.json

    assert sess_event["attrs"] == {
        "release": "test-release",
        "environment": "test-env",
    }
    # Request sessions show up as aggregates
    assert "aggregates" in sess_event
