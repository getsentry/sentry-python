# -*- coding: utf-8 -*-
from __future__ import absolute_import

from django.dispatch.dispatcher import NO_RECEIVERS
from sentry_sdk import Hub

from django.dispatch import Signal


def patch_signals():
    """Patch django signal receivers to create a span"""

    def send(self: Signal,  sender, **kwargs):
        if not self.receivers or self.sender_receivers_cache.get(sender) is NO_RECEIVERS:
            return []
        results = []
        hub = Hub.current
        for receiver in self._live_receivers(sender):
            name = receiver.__module__ + "."
            if hasattr(receiver, "__name__"):
                name += receiver.__name__
            else:
                name += str(receiver)
            with hub.start_span(
                op="django.signals",
                description=name,
            ) as span:
                span.set_data("signal", name)
                results.append((receiver, receiver(signal=self, sender=sender, **kwargs)))
        return results

    Signal.send = send
