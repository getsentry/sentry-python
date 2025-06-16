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


def test_session_with_error(sentry_init, capture_envelopes):
    """Test that sessions capture errors correctly."""
    sentry_init(release="test-release", environment="test-env")
    envelopes = capture_envelopes()

    # Start a session
    start_session()
    
    # Capture an exception
    try:
        raise ValueError("test error")
    except Exception:
        capture_exception()
    
    # End the session
    end_session()
    sentry_sdk.flush()

    # We should have 2 envelopes: one for the error, one for the session
    assert len(envelopes) == 2
    
    # First envelope is the error
    assert envelopes[0].get_event() is not None
    
    # Second envelope is the session
    sess = envelopes[1]
    assert len(sess.items) == 1
    sess_event = sess.items[0].payload.json

    assert sess_event["attrs"] == {
        "release": "test-release",
        "environment": "test-env",
    }
    assert sess_event["errors"] == 1
    assert sess_event["status"] == "exited"


def test_multiple_sessions(sentry_init, capture_envelopes):
    """Test that starting a new session ends the previous one."""
    sentry_init(release="test-release", environment="test-env")
    envelopes = capture_envelopes()

    # Start first session
    start_session()
    
    # Start second session (should end the first)
    start_session()
    
    # End second session
    end_session()
    sentry_sdk.flush()

    # Sessions might be batched together
    assert len(envelopes) >= 1
    
    # Count total sessions across all envelopes
    total_sessions = sum(len(envelope.items) for envelope in envelopes)
    assert total_sessions == 2
    
    # Check session properties
    for envelope in envelopes:
        for item in envelope.items:
            sess_event = item.payload.json
            assert sess_event["attrs"] == {
                "release": "test-release",
                "environment": "test-env",
            }
            assert sess_event["status"] == "exited"


def test_session_user_info(sentry_init, capture_envelopes):
    """Test that sessions include user information."""
    sentry_init(release="test-release", environment="test-env")
    envelopes = capture_envelopes()

    # Set user info
    sentry_sdk.set_user({"id": "123", "email": "test@example.com"})
    
    # Start and end a session
    start_session()
    end_session()
    sentry_sdk.flush()

    assert len(envelopes) == 1
    sess = envelopes[0]
    sess_event = sess.items[0].payload.json
    
    # Session should have user ID
    assert sess_event["did"] == "123"


def test_isolation_scope_isolation(sentry_init, capture_envelopes):
    """Test that sessions are properly isolated in different isolation scopes."""
    sentry_init(release="test-release", environment="test-env")
    envelopes = capture_envelopes()

    # Start a session in the main isolation scope
    start_session()
    
    # Create a new isolation scope
    with sentry_sdk.isolation_scope() as scope:
        # This should not affect the outer session
        scope.set_tag("inner", "true")
        start_session()
        end_session()
    
    # End the outer session
    end_session()
    sentry_sdk.flush()

    # Count unique sessions by their session ID
    unique_sessions = set()
    for envelope in envelopes:
        for item in envelope.items:
            sess_data = item.payload.json
            unique_sessions.add(sess_data["sid"])
    
    # We should have 2 unique sessions: one from outer scope, one from inner scope
    assert len(unique_sessions) == 2


def test_api_matches_scope_behavior(sentry_init, capture_envelopes):
    """Test that the top-level API behaves the same as calling methods on isolation scope."""
    sentry_init(release="test-release", environment="test-env")
    envelopes = capture_envelopes()

    # Using top-level API
    start_session()
    try:
        raise ValueError("test")
    except Exception:
        capture_exception()
    end_session()
    
    # Using isolation scope directly
    sentry_sdk.get_isolation_scope().start_session()
    try:
        raise ValueError("test2")
    except Exception:
        capture_exception()
    sentry_sdk.get_isolation_scope().end_session()
    
    sentry_sdk.flush()

    # We should have at least 2 errors and 2 sessions
    error_envelopes = [e for e in envelopes if e.get_event() is not None]
    assert len(error_envelopes) == 2
    
    # Count total sessions across all envelopes (some might be batched)
    session_items = []
    for envelope in envelopes:
        if envelope.get_event() is None:  # Not an error envelope
            session_items.extend(envelope.items)
    
    assert len(session_items) == 2
    
    # Check that both sessions have the same structure
    for item in session_items:
        sess_event = item.payload.json
        assert sess_event["attrs"] == {
            "release": "test-release",
            "environment": "test-env",
        }
        assert sess_event["errors"] == 1
        assert sess_event["status"] == "exited"