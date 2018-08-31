import os
import socket


def default_shutdown_callback(pending, timeout):
    print("Sentry is attempting to send %i pending error messages" % pending)
    print("Waiting up to %s seconds" % timeout)
    print("Press Ctrl-%s to quit" % (os.name == "nt" and "Break" or "C"))


VERSION = "0.1"
DEFAULT_SERVER_NAME = socket.gethostname() if hasattr(socket, "gethostname") else None
DEFAULT_OPTIONS = {
    "dsn": None,
    "with_locals": True,
    "max_breadcrumbs": 100,
    "release": None,
    "environment": None,
    "server_name": DEFAULT_SERVER_NAME,
    "shutdown_timeout": 2.0,
    "shutdown_callback": default_shutdown_callback,
    "integrations": [],
    "in_app_include": [],
    "in_app_exclude": [],
    "default_integrations": True,
    "repos": {},
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
}

SDK_INFO = {"name": "sentry-python", "version": VERSION}
