import os
import linecache

from datetime import datetime
from collections import Mapping, Sequence

from ._compat import urlparse, text_type, implements_str


epoch = datetime(1970, 1, 1)


def to_timestamp(value):
    return (value - epoch).total_seconds()


class BadDsn(ValueError):
    """Raised on invalid DSNs."""


@implements_str
class Dsn(object):
    """Represents a DSN."""

    def __init__(self, value):
        if isinstance(value, Dsn):
            self.__dict__ = dict(value.__dict__)
            return
        parts = urlparse.urlsplit(text_type(value))
        if parts.scheme not in (u"http", u"https"):
            raise BadDsn("Unsupported scheme %r" % parts.scheme)
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port
        if self.port is None:
            self.port = self.scheme == "https" and 443 or 80
        self.public_key = parts.username
        if not self.public_key:
            raise BadDsn("Missig public key")
        self.secret_key = parts.password
        if not parts.path:
            raise BadDsn("Missing project ID in DSN")
        try:
            self.project_id = text_type(int(parts.path[1:]))
        except (ValueError, TypeError):
            raise BadDsn("Invalid project in DSN (%r)" % (parts.path or "")[1:])

    @property
    def netloc(self):
        """The netloc part of a DSN."""
        rv = self.host
        if (self.scheme, self.port) not in (("http", 80), ("https", 443)):
            rv = "%s:%s" % (rv, self.port)
        return rv

    def to_auth(self, client=None):
        """Returns the auth info object for this dsn."""
        return Auth(
            scheme=self.scheme,
            host=self.netloc,
            project_id=self.project_id,
            public_key=self.public_key,
            secret_key=self.secret_key,
            client=client,
        )

    def __str__(self):
        return "%s://%s%s@%s/%s" % (
            self.scheme,
            self.public_key,
            self.secret_key and "@" + self.secret_key or "",
            self.netloc,
            self.project_id,
        )


class Auth(object):
    """Helper object that represents the auth info."""

    def __init__(
        self,
        scheme,
        host,
        project_id,
        public_key,
        secret_key=None,
        version=7,
        client=None,
    ):
        self.scheme = scheme
        self.host = host
        self.project_id = project_id
        self.public_key = public_key
        self.secret_key = secret_key
        self.version = version
        self.client = client

    @property
    def store_api_url(self):
        """Returns the API url for storing events."""
        return "%s://%s/api/%s/store/" % (self.scheme, self.host, self.project_id)

    def to_header(self, timestamp=None):
        """Returns the auth header a string."""
        rv = [("sentry_key", self.public_key), ("sentry_version", self.version)]
        if timestamp is not None:
            rv.append(("sentry_timestamp", str(to_timestamp(timestamp))))
        if self.client is not None:
            rv.append(("sentry_client", self.client))
        if self.secret_key is not None:
            rv.append(("sentry_secret", self.secret_key))
        return u"Sentry " + u", ".join("%s=%s" % (key, value) for key, value in rv)


def get_type_name(cls):
    return getattr(cls, "__qualname__", None) or getattr(cls, "__name__", None)


def get_type_module(cls):
    mod = getattr(cls, "__module__", None)
    if mod not in (None, "builtins", "__builtins__"):
        return mod


def iter_stacks(tb):
    while tb is not None:
        f_locals = getattr(tb, "f_locals", None)
        skip = False
        try:
            if f_locals["__traceback_hide__"]:
                skip = True
        except Exception:
            pass
        if not skip:
            yield tb
        tb = tb.tb_next


def slim_string(value, length=512):
    if not value:
        return value
    if len(value) > length:
        return value[: length - 3] + "..."
    return value[:length]


def get_lines_from_file(filename, lineno, loader=None, module=None):
    context_lines = 5
    source = None
    if loader is not None and hasattr(loader, "get_source"):
        try:
            source = loader.get_source(module)
        except (ImportError, IOError):
            source = None
        if source is not None:
            source = source.splitlines()

    if source is None:
        try:
            source = linecache.getlines(filename)
        except (OSError, IOError):
            return None, None, None

    if not source:
        return None, None, None

    lower_bound = max(0, lineno - context_lines)
    upper_bound = min(lineno + 1 + context_lines, len(source))

    try:
        pre_context = [
            slim_string(line.strip("\r\n")) for line in source[lower_bound:lineno]
        ]
        context_line = slim_string(source[lineno].strip("\r\n"))
        post_context = [
            slim_string(line.strip("\r\n"))
            for line in source[(lineno + 1) : upper_bound]
        ]
        return pre_context, context_line, post_context
    except IndexError:
        # the file may have changed since it was loaded into memory
        return [], None, []


def get_source_context(frame):
    try:
        abs_path = frame.f_code.co_filename
    except Exception:
        abs_path = None
    try:
        module = frame.f_globals["__name__"]
    except Exception:
        return [], None, []
    try:
        loader = frame.f_globals["__loader__"]
    except Exception:
        loader = None
    lineno = frame.f_lineno - 1
    if lineno is not None and abs_path:
        return get_lines_from_file(abs_path, lineno, loader, module)
    return [], None, []


def skip_internal_frames(frame):
    tb = frame
    while tb is not None:
        try:
            mod = tb.tb_frame.f_globals["__name__"]
            if not mod.startswith("sentry_sdk."):
                break
        except (AttributeError, KeyError):
            pass
        tb = tb.tb_next
    return tb


def safe_str(value):
    try:
        return text_type(value)
    except Exception:
        return safe_repr(value)


def safe_repr(value):
    try:
        rv = repr(value)
        if isinstance(rv, bytes):
            rv = rv.decode("utf-8", "replace")
        try:
            return rv.encode("utf-8").decode("unicode-escape")
        except Exception:
            return rv
    except Exception:
        return u"<broken repr>"


def object_to_json(obj):
    def _walk(obj, depth):
        if depth < 4:
            if isinstance(obj, Sequence) and not isinstance(obj, (bytes, text_type)):
                return [_walk(x, depth + 1) for x in obj]
            if isinstance(obj, Mapping):
                return {safe_str(k): _walk(v, depth + 1) for k, v in obj.items()}
        return safe_repr(obj)

    return _walk(obj, 0)


def extract_locals(frame):
    rv = {}
    for key, value in frame.f_locals.items():
        rv[key] = object_to_json(value)
    return rv


def frame_from_traceback(tb, with_locals=True):
    frame = tb.tb_frame
    f_code = getattr(frame, "f_code", None)
    if f_code:
        abs_path = frame.f_code.co_filename
        function = frame.f_code.co_name
    else:
        abs_path = None
        function = None
    try:
        module = frame.f_globals["__name__"]
    except Exception:
        module = None

    pre_context, context_line, post_context = get_source_context(frame)

    rv = {
        "filename": abs_path and os.path.basename(abs_path) or None,
        "abs_path": abs_path,
        "function": function or "<unknown>",
        "module": module,
        "lineno": tb.tb_lineno,
        "pre_context": pre_context,
        "context_line": context_line,
        "post_context": post_context,
    }
    if with_locals:
        rv["vars"] = extract_locals(frame)
    return rv


def stacktrace_from_traceback(tb, with_locals=True):
    return {"frames": [frame_from_traceback(tb, with_locals) for tb in iter_stacks(tb)]}


def single_exception_from_error_tuple(exc_type, exc_value, tb, with_locals=True):
    return {
        "module": get_type_module(exc_type),
        "type": get_type_name(exc_type),
        "value": safe_str(exc_value),
        "stacktrace": stacktrace_from_traceback(tb, with_locals),
    }


def exceptions_from_error_tuple(exc_type, exc_value, tb, with_locals=True):
    rv = []
    while exc_type is not None:
        rv.append(
            single_exception_from_error_tuple(exc_type, exc_value, tb, with_locals)
        )
        cause = getattr(exc_value, "__cause__", None)
        if cause is None:
            break
        exc_type = type(cause)
        exc_value = cause
        tb = getattr(cause, "__traceback__", None)
    return rv


class SkipEvent(Exception):
    """Risen from an event processor to indicate that the event should be
    ignored and not be reported."""


def to_string(value):
    try:
        return text_type(value)
    except UnicodeDecodeError:
        return repr(value)[1:-1]


try:
    from contextvars import ContextVar
except ImportError:
    from threading import local

    class ContextVar(object):
        # Super-limited impl of ContextVar

        def __init__(self, name):
            self._name = name
            self._local = local()

        def get(self, default):
            return getattr(self._local, "value", default)

        def set(self, value):
            setattr(self._local, "value", value)
