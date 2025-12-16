import pytest
import responses

from opentelemetry import trace
from opentelemetry.trace import (
    get_tracer_provider,
    set_tracer_provider,
    ProxyTracerProvider,
    format_span_id,
    format_trace_id,
    get_current_span,
)
from opentelemetry.context import attach, detach
from opentelemetry.propagate import get_global_textmap, set_global_textmap
from opentelemetry.util._once import Once
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from sentry_sdk.integrations.otlp import OTLPIntegration, SentryOTLPPropagator
from sentry_sdk.scope import get_external_propagation_context


original_propagator = get_global_textmap()


@pytest.fixture(autouse=True)
def mock_otlp_ingest():
    responses.start()
    responses.add(
        responses.POST,
        url="https://bla.ingest.sentry.io/api/12312012/integration/otlp/v1/traces/",
        status=200,
    )

    yield

    tracer_provider = get_tracer_provider()
    if isinstance(tracer_provider, TracerProvider):
        tracer_provider.force_flush()

    responses.stop()
    responses.reset()


@pytest.fixture(autouse=True)
def reset_otlp(uninstall_integration):
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace._TRACER_PROVIDER = None

    set_global_textmap(original_propagator)

    uninstall_integration("otlp")


def test_sets_new_tracer_provider_with_otlp_exporter(sentry_init):
    existing_tracer_provider = get_tracer_provider()
    assert isinstance(existing_tracer_provider, ProxyTracerProvider)

    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration()],
    )

    tracer_provider = get_tracer_provider()
    assert tracer_provider is not existing_tracer_provider
    assert isinstance(tracer_provider, TracerProvider)

    (span_processor,) = tracer_provider._active_span_processor._span_processors
    assert isinstance(span_processor, BatchSpanProcessor)

    exporter = span_processor.span_exporter
    assert isinstance(exporter, OTLPSpanExporter)
    assert (
        exporter._endpoint
        == "https://bla.ingest.sentry.io/api/12312012/integration/otlp/v1/traces/"
    )
    assert "X-Sentry-Auth" in exporter._headers
    assert (
        "Sentry sentry_key=mysecret, sentry_version=7, sentry_client=sentry.python/"
        in exporter._headers["X-Sentry-Auth"]
    )


def test_uses_existing_tracer_provider_with_otlp_exporter(sentry_init):
    existing_tracer_provider = TracerProvider()
    set_tracer_provider(existing_tracer_provider)

    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration()],
    )

    tracer_provider = get_tracer_provider()
    assert tracer_provider == existing_tracer_provider
    assert isinstance(tracer_provider, TracerProvider)

    (span_processor,) = tracer_provider._active_span_processor._span_processors
    assert isinstance(span_processor, BatchSpanProcessor)

    exporter = span_processor.span_exporter
    assert isinstance(exporter, OTLPSpanExporter)
    assert (
        exporter._endpoint
        == "https://bla.ingest.sentry.io/api/12312012/integration/otlp/v1/traces/"
    )
    assert "X-Sentry-Auth" in exporter._headers
    assert (
        "Sentry sentry_key=mysecret, sentry_version=7, sentry_client=sentry.python/"
        in exporter._headers["X-Sentry-Auth"]
    )


def test_does_not_setup_exporter_when_disabled(sentry_init):
    existing_tracer_provider = get_tracer_provider()
    assert isinstance(existing_tracer_provider, ProxyTracerProvider)

    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration(setup_otlp_traces_exporter=False)],
    )

    tracer_provider = get_tracer_provider()
    assert tracer_provider is existing_tracer_provider


def test_sets_propagator(sentry_init):
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration()],
    )

    propagator = get_global_textmap()
    assert isinstance(get_global_textmap(), SentryOTLPPropagator)
    assert propagator is not original_propagator


def test_does_not_set_propagator_if_disabled(sentry_init):
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration(setup_propagator=False)],
    )

    propagator = get_global_textmap()
    assert not isinstance(propagator, SentryOTLPPropagator)
    assert propagator is original_propagator


def test_otel_propagation_context(sentry_init):
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration()],
    )

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("foo") as root_span:
        with tracer.start_as_current_span("bar") as span:
            external_propagation_context = get_external_propagation_context()

    assert external_propagation_context is not None
    (trace_id, span_id) = external_propagation_context
    assert trace_id == format_trace_id(root_span.get_span_context().trace_id)
    assert trace_id == format_trace_id(span.get_span_context().trace_id)
    assert span_id == format_span_id(span.get_span_context().span_id)


def test_propagator_inject_head_of_trace(sentry_init):
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration()],
    )

    tracer = trace.get_tracer(__name__)
    propagator = get_global_textmap()
    carrier = {}

    with tracer.start_as_current_span("foo") as span:
        propagator.inject(carrier)

        span_context = span.get_span_context()
        trace_id = format_trace_id(span_context.trace_id)
        span_id = format_span_id(span_context.span_id)

        assert "sentry-trace" in carrier
        assert carrier["sentry-trace"] == f"{trace_id}-{span_id}-1"

        #! we cannot populate baggage in otlp as head SDK yet
        assert "baggage" not in carrier


def test_propagator_inject_continue_trace(sentry_init):
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration()],
    )

    tracer = trace.get_tracer(__name__)
    propagator = get_global_textmap()
    carrier = {}

    incoming_headers = {
        "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
        "baggage": (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700,sentry-sampled=true"
        ),
    }

    ctx = propagator.extract(incoming_headers)
    token = attach(ctx)

    parent_span_context = get_current_span().get_span_context()
    assert (
        format_trace_id(parent_span_context.trace_id)
        == "771a43a4192642f0b136d5159a501700"
    )
    assert format_span_id(parent_span_context.span_id) == "1234567890abcdef"

    with tracer.start_as_current_span("foo") as span:
        propagator.inject(carrier)

        span_context = span.get_span_context()
        trace_id = format_trace_id(span_context.trace_id)
        span_id = format_span_id(span_context.span_id)

        assert trace_id == "771a43a4192642f0b136d5159a501700"

        assert "sentry-trace" in carrier
        assert carrier["sentry-trace"] == f"{trace_id}-{span_id}-1"

        assert "baggage" in carrier
        assert carrier["baggage"] == incoming_headers["baggage"]

    detach(token)


def test_capture_exceptions_enabled(sentry_init, capture_events):
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration(capture_exceptions=True)],
    )

    events = capture_events()

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("test_span") as span:
        try:
            raise ValueError("Test exception")
        except ValueError as e:
            span.record_exception(e)

    (event,) = events
    assert event["exception"]["values"][0]["type"] == "ValueError"
    assert event["exception"]["values"][0]["value"] == "Test exception"
    assert event["exception"]["values"][0]["mechanism"]["type"] == "otlp"
    assert event["exception"]["values"][0]["mechanism"]["handled"] is False

    trace_context = event["contexts"]["trace"]
    assert trace_context["trace_id"] == format_trace_id(
        span.get_span_context().trace_id
    )
    assert trace_context["span_id"] == format_span_id(span.get_span_context().span_id)


def test_capture_exceptions_disabled(sentry_init, capture_events):
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration(capture_exceptions=False)],
    )

    events = capture_events()

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("test_span") as span:
        try:
            raise ValueError("Test exception")
        except ValueError as e:
            span.record_exception(e)

    assert len(events) == 0


def test_capture_exceptions_preserves_otel_behavior(sentry_init, capture_events):
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration(capture_exceptions=True)],
    )

    events = capture_events()

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("test_span") as span:
        try:
            raise ValueError("Test exception")
        except ValueError as e:
            span.record_exception(e, attributes={"foo": "bar"})

        # Verify the span recorded the exception (OpenTelemetry behavior)
        # The span should have events with the exception information
        (otel_event,) = span._events
        assert otel_event.name == "exception"
        assert otel_event.attributes["foo"] == "bar"

    # verify sentry also captured it
    assert len(events) == 1
