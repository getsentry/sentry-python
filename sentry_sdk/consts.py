import socket


VERSION = "0.4.3"
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
}


SDK_INFO = {
    "name": "sentry.python",
    "version": VERSION,
    "packages": [{"name": "pypi:sentry-sdk", "version": VERSION}],
}
