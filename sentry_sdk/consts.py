import socket

VERSION = "0.1"
DEFAULT_SERVER_NAME = socket.gethostname() if hasattr(socket, "gethostname") else None
DEFAULT_OPTIONS = {
    "with_locals": True,
    "max_breadcrumbs": 100,
    "release": None,
    "environment": None,
    "server_name": DEFAULT_SERVER_NAME,
    "drain_timeout": 2.0,
    "integrations": []
}

SDK_INFO = {"name": "sentry-python", "version": VERSION}
