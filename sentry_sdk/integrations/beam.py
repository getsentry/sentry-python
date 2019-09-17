from __future__ import absolute_import

import sys
import types
from functools import wraps

from sentry_sdk.hub import Hub
from sentry_sdk._compat import reraise
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import ignore_logger

WRAPPED_FUNC = "_wrapped_{}_"
INSPECT_FUNC = "_inspect_{}"  # Required format per apache_beam/transforms/core.py
USED_FUNC = "_sentry_used_"


class BeamIntegration(Integration):
    identifier = "beam"

    @staticmethod
    def setup_once():
        # type: () -> None
        from apache_beam.transforms.core import DoFn, ParDo  # type: ignore

        ignore_logger("root")
        ignore_logger("bundle_processor.create")

        function_patches = ["process", "start_bundle", "finish_bundle", "setup"]
        for func_name in function_patches:
            setattr(
                DoFn,
                INSPECT_FUNC.format(func_name),
                _wrap_inspect_call(DoFn, func_name),
            )

        old_init = ParDo.__init__

        def sentry_init_pardo(self, fn, *args, **kwargs):
            # Do not monkey patch init twice
            if not getattr(self, "_sentry_is_patched", False):
                for func_name in function_patches:
                    if not hasattr(fn, func_name):
                        continue
                    wrapped_func = WRAPPED_FUNC.format(func_name)

                    # Check to see if inspect is set and process is not
                    # to avoid monkey patching process twice.
                    # Check to see if function is part of object for
                    # backwards compatibility.
                    process_func = getattr(fn, func_name)
                    inspect_func = getattr(fn, INSPECT_FUNC.format(func_name))
                    if not getattr(inspect_func, USED_FUNC, False) and not getattr(
                        process_func, USED_FUNC, False
                    ):
                        setattr(fn, wrapped_func, process_func)
                        setattr(fn, func_name, _wrap_task_call(process_func))

                self._sentry_is_patched = True
            old_init(self, fn, *args, **kwargs)

        ParDo.__init__ = sentry_init_pardo


def _wrap_inspect_call(cls, func_name):
    from apache_beam.typehints.decorators import getfullargspec  # type: ignore

    if not hasattr(cls, func_name):
        return None

    def _inspect(self):
        """
        Inspect function overrides the way Beam gets argspec.
        """
        wrapped_func = WRAPPED_FUNC.format(func_name)
        if hasattr(self, wrapped_func):
            process_func = getattr(self, wrapped_func)
        else:
            process_func = getattr(self, func_name)
            setattr(self, func_name, _wrap_task_call(process_func))
            setattr(self, wrapped_func, process_func)

        # getfullargspec is deprecated in more recent beam versions and get_function_args_defaults
        # (which uses Signatures internally) should be used instead.
        try:
            from apache_beam.transforms.core import get_function_args_defaults

            return get_function_args_defaults(process_func)
        except ImportError:
            return getfullargspec(process_func)

    setattr(_inspect, USED_FUNC, True)
    return _inspect


def _wrap_task_call(func):
    """
    Wrap task call with a try catch to get exceptions.
    Pass the client on to raise_exception so it can get rebinded.
    """
    client = Hub.current.client

    @wraps(func)
    def _inner(*args, **kwargs):
        try:
            gen = func(*args, **kwargs)
        except Exception:
            raise_exception(client)

        if not isinstance(gen, types.GeneratorType):
            return gen
        return _wrap_generator_call(gen, client)

    setattr(_inner, USED_FUNC, True)
    return _inner


def _capture_exception(exc_info, hub):
    """
    Send Beam exception to Sentry.
    """
    integration = hub.get_integration(BeamIntegration)
    if integration:
        client = hub.client
        event, hint = event_from_exception(
            exc_info,
            client_options=client.options,
            mechanism={"type": "beam", "handled": False},
        )
        hub.capture_event(event, hint=hint)


def raise_exception(client):
    """
    Raise an exception. If the client is not in the hub, rebind it.
    """
    hub = Hub.current
    if hub.client is None:
        hub.bind_client(client)
    exc_info = sys.exc_info()
    with capture_internal_exceptions():
        _capture_exception(exc_info, hub)
    reraise(*exc_info)


def _wrap_generator_call(gen, client):
    """
    Wrap the generator to handle any failures.
    """
    while True:
        try:
            yield next(gen)
        except StopIteration:
            break
        except Exception:
            raise_exception(client)
