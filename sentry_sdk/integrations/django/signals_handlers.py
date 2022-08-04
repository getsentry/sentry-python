# -*- coding: utf-8 -*-
from __future__ import absolute_import

from django.dispatch import Signal

from sentry_sdk import Hub


def patch_signals():
    """Patch django signal receivers to create a span"""

    old_live_receivers = Signal._live_receivers

    def _get_receiver_name(receiver):
        name = receiver.__module__ + "."
        if hasattr(receiver, "__name__"):
            return name + receiver.__name__
        return name + str(receiver)

    def _sentry_live_receivers(self, sender):
        hub = Hub.current
        receivers = old_live_receivers(self, sender)

        def sentry_receiver_wrapper(receiver):
            def wrapper(*args, **kwargs):
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
