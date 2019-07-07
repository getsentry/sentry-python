import os
import sys
import linecache
import logging

from contextlib import contextmanager
from datetime import datetime

from sentry_sdk._compat import urlparse, text_type, implements_str, int_types, PY2

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Iterator
    from typing import List
    from typing import Optional
    from typing import Set
    from typing import Tuple
    from typing import Union
    from types import FrameType
    from types import TracebackType

    import sentry_sdk

    from sentry_sdk._types import ExcInfo

epoch = datetime(1970, 1, 1)


# The logger is created here but initialized in the debug support module
logger = logging.getLogger("sentry_sdk.errors")

MAX_STRING_LENGTH = 512
MAX_FORMAT_PARAM_LENGTH = 128


def _get_debug_hub():
    # type: () -> Optional[sentry_sdk.Hub]
    # This function is replaced by debug.py
    pass


@contextmanager
def capture_internal_exceptions():
    # type: () -> Iterator
    try:
        yield
    except Exception:
        hub = _get_debug_hub()
        if hub is not None:
            hub._capture_internal_exception(sys.exc_info())


def to_timestamp(value):
    # type: (datetime) -> float
    return (value - epoch).total_seconds()


def event_hint_with_exc_info(exc_info=None):
    # type: (Optional[ExcInfo]) -> Dict[str, Optional[ExcInfo]]
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
        # type: (Union[Dsn, str]) -> None
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
            raise BadDsn("Missing public key")
        self.secret_key = parts.password

        path = parts.path.rsplit("/", 1)

        try:
            self.project_id = text_type(int(path.pop()))
        except (ValueError, TypeError):
            raise BadDsn("Invalid project in DSN (%r)" % (parts.path or "")[1:])

        self.path = "/".join(path) + "/"

    @property
    def netloc(self):
        # type: () -> str
        """The netloc part of a DSN."""
        rv = self.host
        if (self.scheme, self.port) not in (("http", 80), ("https", 443)):
            rv = "%s:%s" % (rv, self.port)
        return rv

    def to_auth(self, client=None):
        # type: (Optional[Any]) -> Auth
        """Returns the auth info object for this dsn."""
        return Auth(
            scheme=self.scheme,
            host=self.netloc,
            path=self.path,
            project_id=self.project_id,
            public_key=self.public_key,
            secret_key=self.secret_key,
            client=client,
        )

    def __str__(self):
        # type: () -> str
        return "%s://%s%s@%s%s%s" % (
            self.scheme,
            self.public_key,
            self.secret_key and "@" + self.secret_key or "",
            self.netloc,
            self.path,
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
        path="/",
    ):
        # type: (str, str, str, str, Optional[str], int, Optional[Any], str) -> None
        self.scheme = scheme
        self.host = host
        self.path = path
        self.project_id = project_id
        self.public_key = public_key
        self.secret_key = secret_key
        self.version = version
        self.client = client

    @property
    def store_api_url(self):
        # type: () -> str
        """Returns the API url for storing events."""
        return "%s://%s%sapi/%s/store/" % (
            self.scheme,
            self.host,
            self.path,
            self.project_id,
        )

    def to_header(self, timestamp=None):
        # type: (Optional[datetime]) -> str
        """Returns the auth header a string."""
        rv = [("sentry_key", self.public_key), ("sentry_version", self.version)]
        if timestamp is not None:
            rv.append(("sentry_timestamp", str(to_timestamp(timestamp))))
        if self.client is not None:
            rv.append(("sentry_client", self.client))
        if self.secret_key is not None:
            rv.append(("sentry_secret", self.secret_key))
        return u"Sentry " + u", ".join("%s=%s" % (key, value) for key, value in rv)


class AnnotatedValue(object):
    def __init__(self, value, metadata):
        # type: (Optional[Any], Dict[str, Any]) -> None
        self.value = value
        self.metadata = metadata


def get_type_name(cls):
    # type: (Optional[type]) -> Optional[str]
    return getattr(cls, "__qualname__", None) or getattr(cls, "__name__", None)


def get_type_module(cls):
    # type: (Optional[type]) -> Optional[str]
    mod = getattr(cls, "__module__", None)
    if mod not in (None, "builtins", "__builtins__"):
        return mod
    return None


def should_hide_frame(frame):
    # type: (FrameType) -> bool
    try:
        mod = frame.f_globals["__name__"]
        if mod.startswith("sentry_sdk."):
            return True
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
    # type: (Optional[TracebackType]) -> Iterator[TracebackType]
    tb_ = tb  # type: Optional[TracebackType]
    while tb_ is not None:
        if not should_hide_frame(tb_.tb_frame):
            yield tb_
        tb_ = tb_.tb_next


def slim_string(value, length=MAX_STRING_LENGTH):
    # type: (str, int) -> str
    if not value:
        return value
    if len(value) > length:
        return value[: length - 3] + "..."
    return value[:length]


def get_lines_from_file(
    filename,  # type: str
    lineno,  # type: int
    loader=None,  # type: Optional[Any]
    module=None,  # type: Optional[str]
):
    # type: (...) -> Tuple[List[str], Optional[str], List[str]]
    context_lines = 5
    source = None
    if loader is not None and hasattr(loader, "get_source"):
        try:
            source_str = loader.get_source(module)  # type: Optional[str]
        except (ImportError, IOError):
            source_str = None
        if source_str is not None:
            source = source_str.splitlines()

    if source is None:
        try:
            source = linecache.getlines(filename)
        except (OSError, IOError):
            return [], None, []

    if not source:
        return [], None, []

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
    # type: (FrameType, int) -> Tuple[List[str], Optional[str], List[str]]
    try:
        abs_path = frame.f_code.co_filename  # type: Optional[str]
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
    # type: (Any) -> str
    try:
        return text_type(value)
    except Exception:
        return safe_repr(value)


def safe_repr(value):
    # type: (Any) -> str
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


def filename_for_module(module, abs_path):
    # type: (Optional[str], Optional[str]) -> Optional[str]
    if not abs_path or not module:
        return abs_path

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
    # type: (FrameType, Optional[int], bool) -> Dict[str, Any]
    f_code = getattr(frame, "f_code", None)
    if not f_code:
        abs_path = None
        function = None
    else:
        abs_path = frame.f_code.co_filename
        function = frame.f_code.co_name
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
    }  # type: Dict[str, Any]
    if with_locals:
        rv["vars"] = frame.f_locals
    return rv


def stacktrace_from_traceback(tb=None, with_locals=True):
    # type: (Optional[TracebackType], bool) -> Dict[str, List[Dict[str, Any]]]
    return {
        "frames": [
            serialize_frame(
                tb.tb_frame, tb_lineno=tb.tb_lineno, with_locals=with_locals
            )
            for tb in iter_stacks(tb)
        ]
    }


def current_stacktrace(with_locals=True):
    # type: (bool) -> Any
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
    # type: (BaseException) -> Optional[Any]
    return getattr(exc_value, "errno", None)


def single_exception_from_error_tuple(
    exc_type,  # type: Optional[type]
    exc_value,  # type: Optional[BaseException]
    tb,  # type: Optional[TracebackType]
    client_options=None,  # type: Optional[dict]
    mechanism=None,  # type: Optional[Dict[str, Any]]
):
    # type: (...) -> Dict[str, Any]
    if exc_value is not None:
        errno = get_errno(exc_value)
    else:
        errno = None

    if errno is not None:
        mechanism = mechanism or {}
        mechanism.setdefault("meta", {}).setdefault("errno", {}).setdefault(
            "number", errno
        )

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


HAS_CHAINED_EXCEPTIONS = hasattr(Exception, "__suppress_context__")

if HAS_CHAINED_EXCEPTIONS:

    def walk_exception_chain(exc_info):
        # type: (ExcInfo) -> Iterator[ExcInfo]
        exc_type, exc_value, tb = exc_info

        seen_exceptions = []
        seen_exception_ids = set()  # type: Set[int]

        while (
            exc_type is not None
            and exc_value is not None
            and id(exc_value) not in seen_exception_ids
        ):
            yield exc_type, exc_value, tb

            # Avoid hashing random types we don't know anything
            # about. Use the list to keep a ref so that the `id` is
            # not used for another object.
            seen_exceptions.append(exc_value)
            seen_exception_ids.add(id(exc_value))

            if exc_value.__suppress_context__:
                cause = exc_value.__cause__
            else:
                cause = exc_value.__context__
            if cause is None:
                break
            exc_type = type(cause)
            exc_value = cause
            tb = getattr(cause, "__traceback__", None)


else:

    def walk_exception_chain(exc_info):
        # type: (ExcInfo) -> Iterator[ExcInfo]
        yield exc_info


def exceptions_from_error_tuple(
    exc_info,  # type: ExcInfo
    client_options=None,  # type: Optional[dict]
    mechanism=None,  # type: Optional[Dict[str, Any]]
):
    # type: (...) -> List[Dict[str, Any]]
    exc_type, exc_value, tb = exc_info
    rv = []
    for exc_type, exc_value, tb in walk_exception_chain(exc_info):
        rv.append(
            single_exception_from_error_tuple(
                exc_type, exc_value, tb, client_options, mechanism
            )
        )

    rv.reverse()

    return rv


def to_string(value):
    # type: (str) -> str
    try:
        return text_type(value)
    except UnicodeDecodeError:
        return repr(value)[1:-1]


def iter_event_stacktraces(event):
    # type: (Dict[str, Any]) -> Iterator[Dict[str, Any]]
    if "stacktrace" in event:
        yield event["stacktrace"]
    if "threads" in event:
        for thread in event["threads"].get("values") or ():
            if "stacktrace" in thread:
                yield thread["stacktrace"]
    if "exception" in event:
        for exception in event["exception"].get("values") or ():
            if "stacktrace" in exception:
                yield exception["stacktrace"]


def iter_event_frames(event):
    # type: (Dict[str, Any]) -> Iterator[Dict[str, Any]]
    for stacktrace in iter_event_stacktraces(event):
        for frame in stacktrace.get("frames") or ():
            yield frame


def handle_in_app(event, in_app_exclude=None, in_app_include=None):
    # type: (Dict[str, Any], Optional[List], Optional[List]) -> Dict[str, Any]
    for stacktrace in iter_event_stacktraces(event):
        handle_in_app_impl(
            stacktrace.get("frames"),
            in_app_exclude=in_app_exclude,
            in_app_include=in_app_include,
        )

    return event


def handle_in_app_impl(frames, in_app_exclude, in_app_include):
    # type: (Any, Optional[List], Optional[List]) -> Optional[Any]
    if not frames:
        return None

    any_in_app = False
    for frame in frames:
        in_app = frame.get("in_app")
        if in_app is not None:
            if in_app:
                any_in_app = True
            continue

        module = frame.get("module")
        if not module:
            continue
        elif _module_in_set(module, in_app_include):
            frame["in_app"] = True
            any_in_app = True
        elif _module_in_set(module, in_app_exclude):
            frame["in_app"] = False

    if not any_in_app:
        for frame in frames:
            if frame.get("in_app") is None:
                frame["in_app"] = True

    return frames


def exc_info_from_error(error):
    # type: (Union[BaseException, ExcInfo]) -> ExcInfo
    if isinstance(error, tuple) and len(error) == 3:
        exc_type, exc_value, tb = error
    elif isinstance(error, BaseException):
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

    else:
        raise ValueError()

    return exc_type, exc_value, tb


def event_from_exception(
    exc_info,  # type: Union[BaseException, ExcInfo]
    client_options=None,  # type: Optional[dict]
    mechanism=None,  # type: Optional[Dict[str, Any]]
):
    # type: (...) -> Tuple[Dict[str, Any], Dict[str, Any]]
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
    # type: (str, Optional[List]) -> bool
    if not set:
        return False
    for item in set or ():
        if item == name or name.startswith(item + "."):
            return True
    return False


def strip_string(value, max_length=512):
    # type: (str, int) -> Union[AnnotatedValue, str]
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


def format_and_strip(
    template, params, strip_string=strip_string, max_length=MAX_FORMAT_PARAM_LENGTH
):
    """Format a string containing %s for placeholders and call `strip_string`
    on each parameter. The string template itself does not have a maximum
    length.

    TODO: handle other placeholders, not just %s
    """
    chunks = template.split(u"%s")
    if not chunks:
        raise ValueError("No formatting placeholders found")

    params = params[: len(chunks) - 1]

    if len(params) < len(chunks) - 1:
        raise ValueError("Not enough params.")

    concat_chunks = []
    iter_chunks = iter(chunks)  # type: Optional[Iterator]
    iter_params = iter(params)  # type: Optional[Iterator]

    while iter_chunks is not None or iter_params is not None:
        if iter_chunks is not None:
            try:
                concat_chunks.append(next(iter_chunks))
            except StopIteration:
                iter_chunks = None

        if iter_params is not None:
            try:
                concat_chunks.append(str(next(iter_params)))
            except StopIteration:
                iter_params = None

    return concat_strings(
        concat_chunks, strip_string=strip_string, max_length=max_length
    )


def concat_strings(
    chunks, strip_string=strip_string, max_length=MAX_FORMAT_PARAM_LENGTH
):
    rv_remarks = []  # type: List[Any]
    rv_original_length = 0
    rv_length = 0
    rv = []  # type: List[str]

    def realign_remark(remark):
        return [
            (rv_length + x if isinstance(x, int_types) and i < 4 else x)
            for i, x in enumerate(remark)
        ]

    for chunk in chunks:
        if isinstance(chunk, AnnotatedValue):
            # Assume it's already stripped!
            stripped_chunk = chunk
            chunk = chunk.value
        else:
            stripped_chunk = strip_string(chunk, max_length=max_length)

        if isinstance(stripped_chunk, AnnotatedValue):
            rv_remarks.extend(
                realign_remark(remark) for remark in stripped_chunk.metadata["rem"]
            )
            stripped_chunk_value = stripped_chunk.value
        else:
            stripped_chunk_value = stripped_chunk

        rv_original_length += len(chunk)
        rv_length += len(stripped_chunk_value)  # type: ignore
        rv.append(stripped_chunk_value)  # type: ignore

    rv_joined = u"".join(rv)
    assert len(rv_joined) == rv_length

    if not rv_remarks:
        return rv_joined

    return AnnotatedValue(
        value=rv_joined, metadata={"len": rv_original_length, "rem": rv_remarks}
    )


def _is_threading_local_monkey_patched():
    # type: () -> bool
    try:
        from gevent.monkey import is_object_patched  # type: ignore

        if is_object_patched("_threading", "local"):
            return True
    except ImportError:
        pass

    try:
        from eventlet.patcher import is_monkey_patched  # type: ignore

        if is_monkey_patched("thread"):
            return True
    except ImportError:
        pass

    return False


IS_THREADING_LOCAL_MONKEY_PATCHED = _is_threading_local_monkey_patched()
del _is_threading_local_monkey_patched


def _get_contextvars():
    # () -> (bool, Type)
    """
    Try to import contextvars and use it if it's deemed safe. We should not use
    contextvars if gevent or eventlet have patched thread locals, as
    contextvars are unaffected by that patch.

    https://github.com/gevent/gevent/issues/1407
    """
    if not IS_THREADING_LOCAL_MONKEY_PATCHED:
        try:
            from contextvars import ContextVar  # type: ignore

            if not PY2 and sys.version_info < (3, 7):
                import aiocontextvars  # type: ignore  # noqa

            return True, ContextVar
        except ImportError:
            pass

    from threading import local

    class ContextVar(object):  # type: ignore
        # Super-limited impl of ContextVar

        def __init__(self, name):
            self._name = name
            self._local = local()

        def get(self, default):
            return getattr(self._local, "value", default)

        def set(self, value):
            setattr(self._local, "value", value)

    return False, ContextVar


HAS_REAL_CONTEXTVARS, ContextVar = _get_contextvars()
del _get_contextvars


def transaction_from_function(func):
    # type: (Callable[..., Any]) -> Optional[str]
    # Methods in Python 2
    try:
        return "%s.%s.%s" % (
            func.im_class.__module__,  # type: ignore
            func.im_class.__name__,  # type: ignore
            func.__name__,
        )
    except Exception:
        pass

    func_qualname = (
        getattr(func, "__qualname__", None) or getattr(func, "__name__", None) or None
    )  # type: Optional[str]

    if not func_qualname:
        # No idea what it is
        return None

    # Methods in Python 3
    # Functions
    # Classes
    try:
        return "%s.%s" % (func.__module__, func_qualname)
    except Exception:
        pass

    # Possibly a lambda
    return func_qualname
