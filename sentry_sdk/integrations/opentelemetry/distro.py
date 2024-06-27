"""
IMPORTANT: The contents of this file are part of a proof of concept and as such
are experimental and not suitable for production use. They may be changed or
removed at any time without prior notice.
"""

from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
from sentry_sdk.integrations.opentelemetry.span_processor import SentrySpanProcessor
from sentry_sdk.utils import logger
from sentry_sdk._types import TYPE_CHECKING

try:
    from opentelemetry import trace
    from opentelemetry.instrumentation.distro import BaseDistro  # type: ignore[attr-defined]
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.trace import TracerProvider
except ImportError:
    raise DidNotEnable("opentelemetry not installed")

try:
    from opentelemetry.instrumentation.django import DjangoInstrumentor  # type: ignore
except ImportError:
    DjangoInstrumentor = None

try:
    from opentelemetry.instrumentation.flask import FlaskInstrumentor  # type: ignore
except ImportError:
    FlaskInstrumentor = None

if TYPE_CHECKING:
    # XXX pkg_resources is deprecated, there's a PR to switch to importlib:
    # https://github.com/open-telemetry/opentelemetry-python-contrib/pull/2181
    # we should align this when the PR gets merged
    from pkg_resources import EntryPoint
    from typing import Any


CONFIGURABLE_INSTRUMENTATIONS = {
    DjangoInstrumentor: {"is_sql_commentor_enabled": True},
    FlaskInstrumentor: {"enable_commenter": True},
}


class _SentryDistro(BaseDistro):  # type: ignore[misc]
    def _configure(self, **kwargs):
        # type: (Any) -> None
        provider = TracerProvider()
        provider.add_span_processor(SentrySpanProcessor())
        trace.set_tracer_provider(provider)
        set_global_textmap(SentryPropagator())

    def load_instrumentor(self, entry_point, **kwargs):
        # type: (EntryPoint, Any) -> None
        instrumentor = entry_point.load()

        if instrumentor in CONFIGURABLE_INSTRUMENTATIONS:
            for key, value in CONFIGURABLE_INSTRUMENTATIONS[instrumentor].items():
                kwargs[key] = value

        instrumentor().instrument(**kwargs)
        logger.debug(
            "[OTel] %s instrumented (%s)",
            entry_point.name,
            ", ".join([f"{k}: {v}" for k, v in kwargs.items()]),
        )
