import os
import sys
import linecache
import logging

from contextlib import contextmanager
from datetime import datetime

from sentry_sdk._compat import (
    urlparse,
    text_type,
    implements_str,
    string_types,
    number_types,
    int_types,
    PY2,
)

if PY2:
    # Importing ABCs from collections is deprecated, and will stop working in 3.8
    # https://github.com/python/cpython/blob/master/Lib/collections/__init__.py#L49
    from collections import Mapping, Sequence
else:
    # New in 3.3
    # https://docs.python.org/3/library/collections.abc.html
    from collections.abc import Mapping, Sequence

epoch = datetime(1970, 1, 1)


# The logger is created here but initialized in the debug support module
logger = logging.getLogger("sentry_sdk.errors")


def _get_debug_hub():
    # This function is replaced by debug.py
    pass


@contextmanager
def capture_internal_exceptions():
    try:
        yield
    except Exception:
        hub = _get_debug_hub()
        if hub is not None:
            hub._capture_internal_exception(sys.exc_info())


def to_timestamp(value):
    return (value - epoch).total_seconds()


def event_hint_with_exc_info(exc_info=None):
    """Creates a hint with the exc info filled in."""
    if exc_info is None:
        exc_info = sys.exc_info()
    else:
        exc_info = exc_info_from_error(exc_info)
    if exc_info[0] is None:
        exc_info = None
    return {"exc_info": exc_info}


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


def should_hide_frame(frame):
    try:
        mod = frame.f_globals["__name__"]
        return mod.startswith("sentry_sdk.")
    except (AttributeError, KeyError):
        pass

    for flag_name in "__traceback_hide__", "__tracebackhide__":
        try:
            if frame.f_locals[flag_name]:
                return True
        except Exception:
            pass

    return False


def iter_stacks(tb):
    while tb is not None:
        if not should_hide_frame(tb.tb_frame):
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


def get_source_context(frame, tb_lineno):
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
    lineno = tb_lineno - 1
    if lineno is not None and abs_path:
        return get_lines_from_file(abs_path, lineno, loader, module)
    return [], None, []


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

        # At this point `rv` contains a bunch of literal escape codes, like
        # this (exaggerated example):
        #
        # u"\\x2f"
        #
        # But we want to show this string as:
        #
        # u"/"
        try:
            # unicode-escape does this job, but can only decode latin1. So we
            # attempt to encode in latin1.
            return rv.encode("latin1").decode("unicode-escape")
        except Exception:
            # Since usually strings aren't latin1 this can break. In those
            # cases we just give up.
            return rv
    except Exception:
        # If e.g. the call to `repr` already fails
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
        rv[str(key)] = object_to_json(value)
    return rv


def filename_for_module(module, abs_path):
    try:
        if abs_path.endswith(".pyc"):
            abs_path = abs_path[:-1]

        base_module = module.split(".", 1)[0]
        if base_module == module:
            return os.path.basename(abs_path)

        base_module_path = sys.modules[base_module].__file__
        return abs_path.split(base_module_path.rsplit(os.sep, 2)[0], 1)[-1].lstrip(
            os.sep
        )
    except Exception:
        return abs_path


def serialize_frame(frame, tb_lineno=None, with_locals=True):
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

    if tb_lineno is None:
        tb_lineno = frame.f_lineno

    pre_context, context_line, post_context = get_source_context(frame, tb_lineno)

    rv = {
        "filename": filename_for_module(module, abs_path) or None,
        "abs_path": os.path.abspath(abs_path) if abs_path else None,
        "function": function or "<unknown>",
        "module": module,
        "lineno": tb_lineno,
        "pre_context": pre_context,
        "context_line": context_line,
        "post_context": post_context,
    }
    if with_locals:
        rv["vars"] = extract_locals(frame)
    return rv


def stacktrace_from_traceback(tb=None, with_locals=True):
    return {
        "frames": [
            serialize_frame(
                tb.tb_frame, tb_lineno=tb.tb_lineno, with_locals=with_locals
            )
            for tb in iter_stacks(tb)
        ]
    }


def current_stacktrace(with_locals=True):
    __tracebackhide__ = True
    frames = []

    f = sys._getframe()
    while f is not None:
        if not should_hide_frame(f):
            frames.append(serialize_frame(f, with_locals=with_locals))
        f = f.f_back

    frames.reverse()

    return {"frames": frames}


def get_errno(exc_value):
    return getattr(exc_value, "errno", None)


def single_exception_from_error_tuple(
    exc_type, exc_value, tb, client_options=None, mechanism=None
):
    errno = get_errno(exc_value)
    if errno is not None:
        mechanism = mechanism or {}
        mechanism_meta = mechanism.setdefault("meta", {})
        mechanism_meta.setdefault("errno", {"code": errno})

    if client_options is None:
        with_locals = True
    else:
        with_locals = client_options["with_locals"]

    return {
        "module": get_type_module(exc_type),
        "type": get_type_name(exc_type),
        "value": safe_str(exc_value),
        "mechanism": mechanism,
        "stacktrace": stacktrace_from_traceback(tb, with_locals),
    }


def exceptions_from_error_tuple(exc_info, client_options=None, mechanism=None):
    exc_type, exc_value, tb = exc_info
    rv = []
    while exc_type is not None:
        rv.append(
            single_exception_from_error_tuple(
                exc_type, exc_value, tb, client_options, mechanism
            )
        )
        cause = getattr(exc_value, "__cause__", None)
        if cause is None:
            break
        exc_type = type(cause)
        exc_value = cause
        tb = getattr(cause, "__traceback__", None)
    return rv


def to_string(value):
    try:
        return text_type(value)
    except UnicodeDecodeError:
        return repr(value)[1:-1]


def iter_event_frames(event):
    stacktraces = []
    if "stacktrace" in event:
        stacktraces.append(event["stacktrace"])
    if "exception" in event:
        for exception in event["exception"].get("values") or ():
            if "stacktrace" in exception:
                stacktraces.append(exception["stacktrace"])
    for stacktrace in stacktraces:
        for frame in stacktrace.get("frames") or ():
            yield frame


def handle_in_app(event, in_app_exclude=None, in_app_include=None):
    any_in_app = False
    for frame in iter_event_frames(event):
        in_app = frame.get("in_app")
        if in_app is not None:
            if in_app:
                any_in_app = True
            continue

        module = frame.get("module")
        if not module:
            continue

        if _module_in_set(module, in_app_exclude):
            frame["in_app"] = False
        if _module_in_set(module, in_app_include):
            frame["in_app"] = True
            any_in_app = True

    if not any_in_app:
        for frame in iter_event_frames(event):
            frame["in_app"] = True

    return event


def exc_info_from_error(error):
    if isinstance(error, tuple) and len(error) == 3:
        exc_type, exc_value, tb = error
    else:
        tb = getattr(error, "__traceback__", None)
        if tb is not None:
            exc_type = type(error)
            exc_value = error
        else:
            exc_type, exc_value, tb = sys.exc_info()
            if exc_value is not error:
                tb = None
                exc_value = error
                exc_type = type(error)

    return exc_type, exc_value, tb


def event_from_exception(exc_info, client_options=None, mechanism=None):
    exc_info = exc_info_from_error(exc_info)
    hint = event_hint_with_exc_info(exc_info)
    return (
        {
            "level": "error",
            "exception": {
                "values": exceptions_from_error_tuple(
                    exc_info, client_options, mechanism
                )
            },
        },
        hint,
    )


def _module_in_set(name, set):
    if not set:
        return False
    for item in set or ():
        if item == name or name.startswith(item + "."):
            return True
    return False


class AnnotatedValue(object):
    def __init__(self, value, metadata):
        self.value = value
        self.metadata = metadata


def flatten_metadata(obj):
    def inner(obj):
        if isinstance(obj, Mapping):
            rv = {}
            meta = {}
            for k, v in obj.items():
                # if we actually have "" keys in our data, throw them away. It's
                # unclear how we would tell them apart from metadata
                if k == "":
                    continue

                rv[k], meta[k] = inner(v)
                if meta[k] is None:
                    del meta[k]
                if rv[k] is None:
                    del rv[k]
            return rv, (meta or None)
        if isinstance(obj, Sequence) and not isinstance(obj, (text_type, bytes)):
            rv = []
            meta = {}
            for i, v in enumerate(obj):
                new_v, meta[str(i)] = inner(v)
                rv.append(new_v)
                if meta[str(i)] is None:
                    del meta[str(i)]
            return rv, (meta or None)
        if isinstance(obj, AnnotatedValue):
            return obj.value, {"": obj.metadata}
        return obj, None

    obj, meta = inner(obj)
    if meta is not None:
        obj["_meta"] = meta
    return obj


def strip_event_mut(event):
    strip_stacktrace_mut(event.get("stacktrace", None))
    exception = event.get("exception", None)
    if exception:
        for exception in exception.get("values", None) or ():
            strip_stacktrace_mut(exception.get("stacktrace", None))

    strip_request_mut(event.get("request", None))


def strip_stacktrace_mut(stacktrace):
    if not stacktrace:
        return
    for frame in stacktrace.get("frames", None) or ():
        strip_frame_mut(frame)


def strip_request_mut(request):
    if not request:
        return
    data = request.get("data", None)
    if not data:
        return
    request["data"] = strip_databag(data)


def strip_frame_mut(frame):
    if "vars" in frame:
        frame["vars"] = strip_databag(frame["vars"])


def convert_types(obj):
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(obj, Mapping):
        return {k: convert_types(v) for k, v in obj.items()}
    if isinstance(obj, Sequence) and not isinstance(obj, (text_type, bytes)):
        return [convert_types(v) for v in obj]
    if not isinstance(obj, string_types + number_types):
        return safe_repr(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    return obj


def strip_databag(obj, remaining_depth=20):
    assert not isinstance(obj, bytes), "bytes should have been normalized before"
    if remaining_depth <= 0:
        return AnnotatedValue(None, {"rem": [["!limit", "x"]]})
    if isinstance(obj, text_type):
        return strip_string(obj)
    if isinstance(obj, Mapping):
        return {k: strip_databag(v, remaining_depth - 1) for k, v in obj.items()}
    if isinstance(obj, Sequence):
        return [strip_databag(v, remaining_depth - 1) for v in obj]
    return obj


def strip_string(value, max_length=512):
    # TODO: read max_length from config
    if not value:
        return value
    length = len(value)
    if length > max_length:
        return AnnotatedValue(
            value=value[: max_length - 3] + u"...",
            metadata={
                "len": length,
                "rem": [["!limit", "x", max_length - 3, max_length]],
            },
        )
    return value


def format_and_strip(template, params, strip_string=strip_string):
    """Format a string containing %s for placeholders and call `strip_string`
    on each parameter. The string template itself does not have a maximum
    length.

    TODO: handle other placeholders, not just %s
    """
    chunks = template.split(u"%s")
    if not chunks:
        raise ValueError("No formatting placeholders found")

    params = list(reversed(params))
    rv_remarks = []
    rv_original_length = 0
    rv_length = 0
    rv = []

    def realign_remark(remark):
        return [
            (rv_length + x if isinstance(x, int_types) and i < 4 else x)
            for i, x in enumerate(remark)
        ]

    for chunk in chunks[:-1]:
        rv.append(chunk)
        rv_length += len(chunk)
        rv_original_length += len(chunk)
        if not params:
            raise ValueError("Not enough params.")
        param = params.pop()

        stripped_param = strip_string(param)
        if isinstance(stripped_param, AnnotatedValue):
            rv_remarks.extend(
                realign_remark(remark) for remark in stripped_param.metadata["rem"]
            )
            stripped_param = stripped_param.value

        rv_original_length += len(param)
        rv_length += len(stripped_param)
        rv.append(stripped_param)

    rv.append(chunks[-1])
    rv_length += len(chunks[-1])
    rv_original_length += len(chunks[-1])

    rv = u"".join(rv)
    assert len(rv) == rv_length

    if not rv_remarks:
        return rv

    return AnnotatedValue(
        value=rv, metadata={"len": rv_original_length, "rem": rv_remarks}
    )


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
