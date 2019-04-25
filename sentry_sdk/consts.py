import socket

if False:
    from mypy_extensions import TypedDict
    from typing import Optional
    from typing import Callable
    from typing import Union
    from typing import List

    from sentry_sdk.transport import Transport
    from sentry_sdk.integrations import Integration

    ClientOptions = TypedDict(
        "ClientOptions",
        {
            "dsn": Optional[str],
            "with_locals": bool,
            "max_breadcrumbs": int,
            "release": Optional[str],
            "environment": Optional[str],
            "server_name": Optional[str],
            "shutdown_timeout": int,
            "integrations": List[Integration],
            "in_app_include": List[str],
            "in_app_exclude": List[str],
            "default_integrations": bool,
            "dist": Optional[str],
            "transport": Optional[Union[Transport, type, Callable]],
            "sample_rate": int,
            "send_default_pii": bool,
            "http_proxy": Optional[str],
            "https_proxy": Optional[str],
            "ignore_errors": List[type],
            "request_bodies": str,
            "before_send": Optional[Callable],
            "before_breadcrumb": Optional[Callable],
            "debug": bool,
            "attach_stacktrace": bool,
            "ca_certs": Optional[str],
        },
        total=False,
    )


VERSION = "0.7.12"
DEFAULT_SERVER_NAME = socket.gethostname() if hasattr(socket, "gethostname") else None
DEFAULT_OPTIONS = {
    "dsn": None,
    "with_locals": True,
    "max_breadcrumbs": 100,
    "release": None,
    "environment": None,
    "server_name": DEFAULT_SERVER_NAME,
    "shutdown_timeout": 2.0,
    "integrations": [],
    "in_app_include": [],
    "in_app_exclude": [],
    "default_integrations": True,
    "dist": None,
    "transport": None,
    "sample_rate": 1.0,
    "send_default_pii": False,
    "http_proxy": None,
    "https_proxy": None,
    "ignore_errors": [],
    "request_bodies": "medium",
    "before_send": None,
    "before_breadcrumb": None,
    "debug": False,
    "attach_stacktrace": False,
    "ca_certs": None,
}


SDK_INFO = {
    "name": "sentry.python",
    "version": VERSION,
    "packages": [{"name": "pypi:sentry-sdk", "version": VERSION}],
}
