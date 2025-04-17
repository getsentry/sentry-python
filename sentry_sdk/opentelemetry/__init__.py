from opentelemetry.version import __version__ as OTEL_VERSION

from sentry_sdk.opentelemetry.propagator import SentryPropagator
from sentry_sdk.opentelemetry.sampler import SentrySampler
from sentry_sdk.opentelemetry.span_processor import SentrySpanProcessor
from sentry_sdk.utils import parse_version

__all__ = [
    "SentryPropagator",
    "SentrySampler",
    "SentrySpanProcessor",
]

OTEL_VERSION = parse_version(OTEL_VERSION)
