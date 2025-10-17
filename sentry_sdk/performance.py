from contextlib import contextmanager

# Import the helper that returns the current active span/transaction without
# importing the top-level package to avoid circular imports.
from sentry_sdk.tracing_utils import get_current_span


@contextmanager
def allow_n_plus_one(reason=None):
    """Context manager to mark the current span and its root transaction as
    intentionally allowed N+1.

    This sets tags on the active span and its containing transaction so that
    server-side N+1 detectors (if updated to honor these tags) can ignore the
    transaction. This helper is best-effort and will not raise if there is no
    active span/transaction.

    Usage:
        with allow_n_plus_one("expected loop"):
            for x in queryset:
                ...
    """
    span = get_current_span()
    if span is not None:
        try:
            # Tag the active span
            span.set_tag("sentry.n_plus_one.ignore", True)
            if reason:
                span.set_tag("sentry.n_plus_one.reason", reason)

            # Also tag the containing transaction if available
            try:
                tx = span.containing_transaction
            except Exception:
                tx = None

            if tx is not None:
                try:
                    tx.set_tag("sentry.n_plus_one.ignore", True)
                    if reason:
                        tx.set_tag("sentry.n_plus_one.reason", reason)
                except Exception:
                    # best-effort: do not fail if transaction tagging fails
                    pass
        except Exception:
            # best-effort: silence any unexpected errors
            pass

    try:
        yield
    finally:
        # keep tags; no cleanup required
        pass
