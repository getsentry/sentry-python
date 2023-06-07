import sentry_sdk
from sentry_sdk.tracing import NoOpSpan

# This tests make sure, that the examples from the documentation [1]
# are working when OTel (OpenTelementry) instrumentation is turned on
# and therefore the Senntry tracing should not do anything.
#
# 1: https://docs.sentry.io/platforms/python/performance/instrumentation/custom-instrumentation/


def test_noop_start_transaction(sentry_init):
    sentry_init(instrumenter="otel", debug=True)

    with sentry_sdk.start_transaction(
        op="task", name="test_transaction_name"
    ) as transaction:
        assert isinstance(transaction, NoOpSpan)
        assert sentry_sdk.Hub.current.scope.span is transaction

        transaction.name = "new name"


def test_noop_start_span(sentry_init):
    sentry_init(instrumenter="otel", debug=True)

    with sentry_sdk.start_span(op="http", description="GET /") as span:
        assert isinstance(span, NoOpSpan)
        assert sentry_sdk.Hub.current.scope.span is span

        span.set_tag("http.response.status_code", 418)
        span.set_data("http.entity_type", "teapot")


def test_noop_transaction_start_child(sentry_init):
    sentry_init(instrumenter="otel", debug=True)

    transaction = sentry_sdk.start_transaction(name="task")
    assert isinstance(transaction, NoOpSpan)

    with transaction.start_child(op="child_task") as child:
        assert isinstance(child, NoOpSpan)
        assert sentry_sdk.Hub.current.scope.span is child


def test_noop_span_start_child(sentry_init):
    sentry_init(instrumenter="otel", debug=True)
    span = sentry_sdk.start_span(name="task")
    assert isinstance(span, NoOpSpan)

    with span.start_child(op="child_task") as child:
        assert isinstance(child, NoOpSpan)
        assert sentry_sdk.Hub.current.scope.span is child
