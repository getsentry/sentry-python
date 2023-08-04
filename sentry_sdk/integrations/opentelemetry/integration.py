import sys
from importlib import import_module

from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.opentelemetry.span_processor import SentrySpanProcessor
from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
from sentry_sdk.integrations.modules import _get_installed_modules
from sentry_sdk.utils import logger

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


INSTRUMENTED_CLASSES = {
    # A mapping of packages to (original class, instrumented class) pairs. This
    # is used to instrument any classes that were imported before OTel
    # instrumentation took place.
    # XXX otel: mappings need to be added manually
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
        _record_unpatched_classes()

        try:
            distro = _load_distro()
            distro.configure()
            _load_instrumentors(distro)
        except Exception:
            logger.exception("Failed to auto initialize opentelemetry")

        _patch_remaining_classes()
        _setup_sentry_tracing()

        logger.debug("Finished setting up opentelemetry integration")


def _record_unpatched_classes():
    # type: () -> None
    """Keep references to classes that are about to be instrumented."""
    installed_packages = _get_installed_modules()

    for package, (orig_path, instr_path) in INSTRUMENTED_CLASSES.items():
        if package in installed_packages:
            # this package is likely to get instrumented, let's remember the
            # unpatched classes so that we can re-patch any missing occurrences
            # later on
            parts = orig_path.rsplit(".", maxsplit=1)
            try:
                orig = getattr(import_module(parts[0]), parts[1])
                INSTRUMENTED_CLASSES[package] = (orig, instr_path)
            except (AttributeError, ImportError):
                logger.debug("[OTel] Failed to import %s", orig_path)


def _patch_remaining_classes():
    # type: () -> None
    """
    Best-effort attempt to patch any uninstrumented classes in sys.modules.

    This enables us to not care about the order of imports and sentry_sdk.init()
    in user code. If e.g. the Flask class had been imported before sentry_sdk
    was init()ed (and therefore before the OTel instrumentation ran), we would
    find and replace it here.

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

        for orig, instr_path in INSTRUMENTED_CLASSES.values():
            if isinstance(orig, str):
                # XXX otel: actually check that it's been instrumented before
                # patching! this just means that autoinstrumentation was
                # attempted, but it might have failed due to version mismatches
                # etc.
                continue

            for var_name, var in vars(module).copy().items():
                if var == orig:
                    logger.debug(
                        "Additionally patching %s from %s with %s",
                        orig,
                        module_name,
                        instr_path,
                    )

                    parts = instr_path.rsplit(".", maxsplit=1)
                    try:
                        instr = getattr(import_module(parts[0]), parts[-1])
                    except (AttributeError, ImportError):
                        logger.debug("[OTel] Failed to import %s", instr_path)

                    setattr(module, var_name, instr)


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
