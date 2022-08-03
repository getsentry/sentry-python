# -*- coding: utf-8 -*-
from __future__ import absolute_import

import logging

from django.dispatch import Signal
from django.dispatch.dispatcher import NO_RECEIVERS

from sentry_sdk import Hub


def patch_signals():
    """Patch django signal receivers to create a span"""

    logger = logging.getLogger("django.dispatch")

    def send(self: Signal, sender, **kwargs):
        if (
            not self.receivers
            or self.sender_receivers_cache.get(sender) is NO_RECEIVERS
        ):
            return []
        responses = []
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
                responses.append(
                    (receiver, receiver(signal=self, sender=sender, **kwargs))
                )
        return responses

    def send_robust(self: Signal, sender, **kwargs):
        if (
            not self.receivers
            or self.sender_receivers_cache.get(sender) is NO_RECEIVERS
        ):
            return []
        hub = Hub.current
        # Call each receiver with whatever arguments it can accept.
        # Return a list of tuple pairs [(receiver, response), ... ].
        responses = []
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
                try:
                    response = receiver(signal=self, sender=sender, **kwargs)
                except Exception as err:
                    logger.error(
                        "Error calling %s in Signal.send_robust() (%s)",
                        receiver.__qualname__,
                        err,
                        exc_info=err,
                    )
                    responses.append((receiver, err))
                else:
                    responses.append((receiver, response))
        return responses

    Signal.send = send
    Signal.send_robust = send_robust
