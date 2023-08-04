import sys
from importlib import import_module

from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.opentelemetry.span_processor import SentrySpanProcessor
from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
from sentry_sdk.integrations.modules import _get_installed_modules
from sentry_sdk.utils import logger
from sentry_sdk._types import TYPE_CHECKING

try:
    from opentelemetry import trace  # type: ignore
    from opentelemetry.instrumentation.auto_instrumentation._load import (  # type: ignore
        _load_distro,
        _load_instrumentors,
    )
    from opentelemetry.propagate import set_global_textmap  # type: ignore
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore
    from opentelemetry.sdk.trace.export import (  # type: ignore
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
except ImportError:
    raise DidNotEnable("opentelemetry not installed")

if TYPE_CHECKING:
    from typing import Dict


INSTRUMENTED_CLASSES = {
    # A mapping of packages to (original class, instrumented class) pairs. This
    # is used to instrument any classes that were imported before OTel
    # instrumentation took place.
    # XXX otel: class mappings need to be added manually
    "fastapi": (
        "fastapi.FastAPI",
        "opentelemetry.instrumentation.fastapi._InstrumentedFastAPI",
    ),
    "flask": ("flask.Flask", "opentelemetry.instrumentation.flask._InstrumentedFlask"),
}


class OpenTelemetryIntegration(Integration):
    identifier = "opentelemetry"

    @staticmethod
    def setup_once():
        # type: () -> None
        logger.warning(
            "[OTel] Initializing highly experimental OpenTelemetry support. Use at your own risk."
        )

        original_classes = _record_unpatched_classes()

        try:
            distro = _load_distro()
            distro.configure()
            _load_instrumentors(distro)
        except Exception:
            logger.exception("[OTel] Failed to auto-initialize OpenTelemetry")

        _patch_remaining_classes(original_classes)
        _setup_sentry_tracing()

        logger.debug("[OTel] Finished setting up OpenTelemetry integration")


def _record_unpatched_classes():
    # type: () -> Dict[str, type]
    """Keep references to classes that are about to be instrumented."""
    installed_packages = _get_installed_modules()

    original_classes = {}

    for package, (orig_path, _) in INSTRUMENTED_CLASSES.items():
        if package in installed_packages:
            # this package is likely to get instrumented, let's remember the
            # unpatched classes so that we can re-patch any missing occurrences
            # later on
            try:
                original_cls = _import_by_path(orig_path)
            except (AttributeError, ImportError):
                logger.debug("[OTel] Failed to import %s", orig_path)
                continue

            original_classes[package] = original_cls

    return original_classes


def _patch_remaining_classes(original_classes):
    # type: (Dict[str, type]) -> None
    """
    Best-effort attempt to patch any uninstrumented classes in sys.modules.

    This enables us to not care about the order of imports and sentry_sdk.init()
    in user code. If e.g. the Flask class had been imported before sentry_sdk
    was init()ed (and therefore before the OTel instrumentation ran), it would
    not be instrumented. This function goes over remaining uninstrumented
    occurrences of the class in sys.modules and patches them.

    Since this is looking for exact matches, it will not work in some scenarios
    (e.g. if someone is not using the specific class explicitly, but rather
    inheriting from it). In those cases it's still necessary to sentry_sdk.init()
    before importing anything that's supposed to be instrumented.
    """
    for module_name, module in sys.modules.copy().items():
        if (
            module_name.startswith("sentry_sdk")
            or module_name in sys.builtin_module_names
        ):
            continue

        for package, original_cls in original_classes.items():
            original_path, instrumented_path = INSTRUMENTED_CLASSES[package]

            try:
                cls = _import_by_path(original_path)
            except (AttributeError, ImportError):
                logger.debug(
                    "[OTel] Failed to check if class has been instrumented: %s",
                    original_path,
                )
                continue

            if not cls.__module__.startswith("opentelemetry."):
                # the class wasn't instrumented, don't do any additional patching
                continue

            for var_name, var in vars(module).copy().items():
                if var == original_cls:
                    logger.debug(
                        "[OTel] Additionally patching %s from %s with %s",
                        original_cls,
                        module_name,
                        instrumented_path,
                    )

                    try:
                        isntrumented_cls = _import_by_path(instrumented_path)
                    except (AttributeError, ImportError):
                        logger.debug("[OTel] Failed to import %s", instrumented_path)

                    setattr(module, var_name, isntrumented_cls)


def _import_by_path(path):
    # type: (str) -> type
    parts = path.rsplit(".", maxsplit=1)
    return getattr(import_module(parts[0]), parts[-1])


def _setup_sentry_tracing():
    # type: () -> None

    provider = TracerProvider()

    # XXX for debugging
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    # XXX end

    provider.add_span_processor(SentrySpanProcessor())

    trace.set_tracer_provider(provider)

    set_global_textmap(SentryPropagator())
