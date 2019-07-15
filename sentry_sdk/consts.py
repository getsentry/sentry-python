from sentry_sdk._types import MYPY

if MYPY:
    from typing import Optional
    from typing import Callable
    from typing import Union
    from typing import List
    from typing import Type
    from typing import Dict
    from typing import Any

    from sentry_sdk.transport import Transport
    from sentry_sdk.integrations import Integration

    from sentry_sdk._types import Event, EventProcessor, BreadcrumbProcessor


# This type exists to trick mypy and PyCharm into thinking `init` and `Client`
# take these arguments (even though they take opaque **kwargs)
class ClientConstructor(object):
    def __init__(
        self,
        dsn=None,  # type: Optional[str]
        with_locals=True,  # type: bool
        max_breadcrumbs=100,  # type: int
        release=None,  # type: Optional[str]
        environment=None,  # type: Optional[str]
        server_name=None,  # type: Optional[str]
        shutdown_timeout=2,  # type: int
        integrations=[],  # type: List[Integration]
        in_app_include=[],  # type: List[str]
        in_app_exclude=[],  # type: List[str]
        default_integrations=True,  # type: bool
        dist=None,  # type: Optional[str]
        transport=None,  # type: Optional[Union[Transport, Type[Transport], Callable[[Event], None]]]
        sample_rate=1.0,  # type: float
        send_default_pii=False,  # type: bool
        http_proxy=None,  # type: Optional[str]
        https_proxy=None,  # type: Optional[str]
        ignore_errors=[],  # type: List[Union[type, str]]
        request_bodies="medium",  # type: str
        before_send=None,  # type: Optional[EventProcessor]
        before_breadcrumb=None,  # type: Optional[BreadcrumbProcessor]
        debug=False,  # type: bool
        attach_stacktrace=False,  # type: bool
        ca_certs=None,  # type: Optional[str]
        propagate_traces=True,  # type: bool
        # DO NOT ENABLE THIS RIGHT NOW UNLESS YOU WANT TO EXCEED YOUR EVENT QUOTA IMMEDIATELY
        traces_sample_rate=0.0,  # type: float
        traceparent_v2=False,  # type: bool
    ):
        # type: (...) -> None
        pass


def _get_default_options():
    # type: () -> Dict[str, Any]
    import inspect

    if hasattr(inspect, "getfullargspec"):
        getargspec = inspect.getfullargspec  # type: ignore
    else:
        getargspec = inspect.getargspec  # type: ignore

    a = getargspec(ClientConstructor.__init__)
    return dict(zip(a.args[-len(a.defaults) :], a.defaults))


DEFAULT_OPTIONS = _get_default_options()
del _get_default_options


VERSION = "0.10.2"
SDK_INFO = {
    "name": "sentry.python",
    "version": VERSION,
    "packages": [{"name": "pypi:sentry-sdk", "version": VERSION}],
}
