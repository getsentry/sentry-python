"""
The Sentry SDK is the new-style SDK for [sentry.io](https://sentry.io/).  It implements
the unified API that all modern SDKs follow for Python 2.7 and 3.5 or later.

The user documentation can be found on [docs.sentry.io](https://docs.sentry.io/).

## Quickstart

The only thing to get going is to call `sentry_sdk.init()`.  When not passed any
arguments the default options are used and the DSN is picked up from the `SENTRY_DSN`
environment variable.  Otherwise the DSN can be passed with the `dsn` keyword
or first argument.

    import sentry_sdk
    sentry_sdk.init()

This initializes the default integrations which will automatically pick up any
uncaught exceptions.  Additionally you can report arbitrary other exceptions:

    try:
        my_failing_function()
    except Exception as e:
        sentry_sdk.capture_exception(e)
"""
from sentry_sdk.hub import Hub, init
from sentry_sdk.scope import Scope
from sentry_sdk.transport import Transport, HttpTransport
from sentry_sdk.client import Client

from sentry_sdk.api import *  # noqa
from sentry_sdk.api import __all__ as api_all

from sentry_sdk.consts import VERSION  # noqa

__all__ = api_all + [  # noqa
    "Hub",
    "Scope",
    "Client",
    "Transport",
    "HttpTransport",
    "init",
    "integrations",
]

# Initialize the debug support after everything is loaded
from sentry_sdk.debug import init_debug_support

init_debug_support()
del init_debug_support
