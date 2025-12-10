import pytest
import responses

from opentelemetry import trace
from opentelemetry.trace import (
    get_tracer_provider,
    set_tracer_provider,
    ProxyTracerProvider,
    format_span_id,
    format_trace_id,
)
from opentelemetry.propagate import get_global_textmap, set_global_textmap
from opentelemetry.util._once import Once
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from sentry_sdk.integrations.otlp import OTLPIntegration
from sentry_sdk.integrations.opentelemetry import SentryPropagator
from sentry_sdk.scope import get_external_propagation_context


original_propagator = get_global_textmap()


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
    assert isinstance(get_global_textmap(), SentryPropagator)
    assert propagator is not original_propagator


def test_does_not_set_propagator_if_disabled(sentry_init):
    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration(setup_propagator=False)],
    )

    propagator = get_global_textmap()
    assert not isinstance(propagator, SentryPropagator)
    assert propagator is original_propagator


@responses.activate
def test_otel_propagation_context(sentry_init):
    responses.add(
        responses.POST,
        url="https://bla.ingest.sentry.io/api/12312012/integration/otlp/v1/traces/",
        status=200,
    )

    sentry_init(
        dsn="https://mysecret@bla.ingest.sentry.io/12312012",
        integrations=[OTLPIntegration()],
    )

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("foo") as root_span:
        with tracer.start_as_current_span("bar") as span:
            external_propagation_context = get_external_propagation_context()

    # Force flush to ensure spans are exported while mock is active
    get_tracer_provider().force_flush()

    assert external_propagation_context is not None
    (trace_id, span_id) = external_propagation_context
    assert trace_id == format_trace_id(root_span.get_span_context().trace_id)
    assert trace_id == format_trace_id(span.get_span_context().trace_id)
    assert span_id == format_span_id(span.get_span_context().span_id)
