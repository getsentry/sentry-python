from __future__ import absolute_import

import weakref

from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception

from rq.timeouts import JobTimeoutException
from rq.worker import Worker


class RqIntegration(Integration):
    identifier = "rq"

    @staticmethod
    def setup_once():

        old_perform_job = Worker.perform_job

        def sentry_patched_perform_job(self, job, *args, **kwargs):
            hub = Hub.current
            integration = hub.get_integration(RqIntegration)

            if integration is None:
                return old_perform_job(self, job, *args, **kwargs)

            with hub.push_scope() as scope:
                scope.add_event_processor(_make_event_processor(weakref.ref(job)))
                rv = old_perform_job(self, job, *args, **kwargs)

            if self.is_horse:
                # We're inside of a forked process and RQ is
                # about to call `os._exit`. Make sure that our
                # events get sent out.
                #
                # Closing the client should not affect other jobs since
                # we're in a different process
                hub.client.close()

            return rv

        Worker.perform_job = sentry_patched_perform_job

        old_handle_exception = Worker.handle_exception

        def sentry_patched_handle_exception(self, job, *exc_info, **kwargs):
            _capture_exception(exc_info)
            return old_handle_exception(self, job, *exc_info, **kwargs)

        Worker.handle_exception = sentry_patched_handle_exception


def _make_event_processor(weak_job):
    def event_processor(event, hint):
        job = weak_job()
        if job is not None:
            with capture_internal_exceptions():
                if "transaction" not in event:
                    event["transaction"] = job.func_name

            with capture_internal_exceptions():
                extra = event.setdefault("extra", {})
                extra["rq-job"] = {
                    "job_id": job.id,
                    "func": job.func_name,
                    "args": job.args,
                    "kwargs": job.kwargs,
                    "description": job.description,
                }

        if "exc_info" in hint:
            with capture_internal_exceptions():
                if issubclass(hint["exc_info"][0], JobTimeoutException):
                    event["fingerprint"] = ["rq", "JobTimeoutException", job.func_name]

        return event

    return event_processor


def _capture_exception(exc_info, **kwargs):
    hub = Hub.current
    if hub.get_integration(RqIntegration) is None:
        return
    event, hint = event_from_exception(
        exc_info,
        client_options=hub.client.options,
        mechanism={"type": "rq", "handled": False},
    )

    hub.capture_event(event, hint=hint)
