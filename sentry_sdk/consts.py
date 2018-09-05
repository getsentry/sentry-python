import sys
import socket
import platform


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


# Modified by sentry_sdk.integrations
INTEGRATIONS = []


def _get_packages():
    yield "python", ".".join(str(x) for x in sys.version_info[:3])
    if hasattr(sys, "pypy_version_info"):
        yield "pypy", ".".join(str(x) for x in sys.pypy_version_info[:3])


SDK_INFO = {
    "name": "sentry.python",
    "version": VERSION,
    "packages": [
        {"package_name": package_name, "version": version}
        for package_name, version in _get_packages()
    ],
    "integrations": INTEGRATIONS,
}


del _get_packages
