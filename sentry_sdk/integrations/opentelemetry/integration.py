"""
IMPORTANT: The contents of this file are part of a proof of concept and as such
are experimental and not suitable for production use. They may be changed or
removed at any time without prior notice.
"""

import warnings
from typing import TYPE_CHECKING

from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
from sentry_sdk.integrations.opentelemetry.span_processor import SentrySpanProcessor
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import logger

if TYPE_CHECKING:
    from typing import Any, Dict, Optional

try:
    from opentelemetry import trace
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.trace import TracerProvider
except ImportError:
    raise DidNotEnable("opentelemetry not installed")

try:
    from opentelemetry.instrumentation.django import (  # type: ignore[import-not-found]
        DjangoInstrumentor,
    )
except ImportError:
    DjangoInstrumentor = None


CONFIGURABLE_INSTRUMENTATIONS = {
    DjangoInstrumentor: {"is_sql_commentor_enabled": True},
}


class OpenTelemetryIntegration(Integration):
    identifier = "opentelemetry"

    @staticmethod
    def setup_once() -> None:
        pass

    def setup_once_with_options(
        self, options: "Optional[Dict[str, Any]]" = None
    ) -> None:
        if has_span_streaming_enabled(options):
            logger.warning(
                "[OTel] OpenTelemetryIntegration is not compatible with span streaming "
                "(trace_lifecycle='stream') and will be disabled."
            )
            return

        warnings.warn(
            "OpenTelemetryIntegration is deprecated. "
            "Please use OTLPIntegration instead: "
            "https://docs.sentry.io/platforms/python/integrations/otlp/",
            DeprecationWarning,
            stacklevel=2,
        )

        _setup_sentry_tracing()
        # _setup_instrumentors()

        logger.debug("[OTel] Finished setting up OpenTelemetry integration")


def _setup_sentry_tracing() -> None:
    provider = TracerProvider()
    provider.add_span_processor(SentrySpanProcessor())
    trace.set_tracer_provider(provider)
    set_global_textmap(SentryPropagator())


def _setup_instrumentors() -> None:
    for instrumentor, kwargs in CONFIGURABLE_INSTRUMENTATIONS.items():
        if instrumentor is None:
            continue
        instrumentor().instrument(**kwargs)
