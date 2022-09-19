# -*- coding: utf-8 -*-
from __future__ import absolute_import

from django.dispatch import Signal

from sentry_sdk import Hub
from sentry_sdk._types import MYPY


if MYPY:
    from typing import Any
    from typing import Callable
    from typing import List


def patch_signals():
    # type: () -> None
    """Patch django signal receivers to create a span"""

    old_live_receivers = Signal._live_receivers

    def _get_receiver_name(receiver):
        # type: (Callable[..., Any]) -> str
        name = receiver.__module__ + "."
        if hasattr(receiver, "__name__"):
            return name + receiver.__name__
        return name + str(receiver)

    def _sentry_live_receivers(self, sender):
        # type: (Signal, Any) -> List[Callable[..., Any]]
        hub = Hub.current
        receivers = old_live_receivers(self, sender)

        def sentry_receiver_wrapper(receiver):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            def wrapper(*args, **kwargs):
                # type: (Any, Any) -> Any
                with hub.start_span(
                    op="django.signals",
                    description=_get_receiver_name(receiver),
                ) as span:
                    span.set_data("signal", _get_receiver_name(receiver))
                    return receiver(*args, **kwargs)

            return wrapper

        for idx, receiver in enumerate(receivers):
            receivers[idx] = sentry_receiver_wrapper(receiver)

        return receivers

    Signal._live_receivers = _sentry_live_receivers
