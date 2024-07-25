import sentry_sdk
import pytest


def test_standalone_span_iter_headers(sentry_init):
    sentry_init(enable_tracing=True)

    with sentry_sdk.start_span(op="test") as span:
        with pytest.raises(StopIteration):
            # We should not have any propagation headers
            next(span.iter_headers())


def test_span_in_span_iter_headers(sentry_init):
    sentry_init(enable_tracing=True)

    with sentry_sdk.start_span(op="test"):
        with sentry_sdk.start_span(op="test2") as span_inner:
            with pytest.raises(StopIteration):
                # We should not have any propagation headers
                next(span_inner.iter_headers())


def test_span_in_transaction(sentry_init):
    sentry_init(enable_tracing=True)

    with sentry_sdk.start_transaction(op="test"):
        with sentry_sdk.start_span(op="test2") as span:
            # Ensure the headers are there
            next(span.iter_headers())


def test_span_in_span_in_transaction(sentry_init):
    sentry_init(enable_tracing=True)

    with sentry_sdk.start_transaction(op="test"):
        with sentry_sdk.start_span(op="test2"):
            with sentry_sdk.start_span(op="test3") as span_inner:
                # Ensure the headers are there
                next(span_inner.iter_headers())
