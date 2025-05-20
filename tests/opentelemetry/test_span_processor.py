import sentry_sdk


def test_span_processor_omits_underscore_attributes(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    with sentry_sdk.start_span():
        with sentry_sdk.start_span() as span:
            span.set_attribute("_internal", 47)
            span.set_attribute("noninternal", 23)

    assert span._otel_span.attributes["_internal"] == 47
    assert span._otel_span.attributes["noninternal"] == 23

    outgoing_span = events[0]["spans"][0]
    assert "_internal" not in outgoing_span["data"]
    assert "noninternal" in outgoing_span["data"]
