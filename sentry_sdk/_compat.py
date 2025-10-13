import sys
import asyncio
import inspect

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from typing import Any
    from typing import TypeVar
    from typing import Callable

    T = TypeVar("T")
    _F = TypeVar("_F", bound=Callable[..., Any])

# Public shim symbols with precise types so mypy accepts branch assignments
iscoroutinefunction: "Callable[[Any], bool]"
markcoroutinefunction: "Callable[[ _F ], _F]"


PY37 = sys.version_info[0] == 3 and sys.version_info[1] >= 7
PY38 = sys.version_info[0] == 3 and sys.version_info[1] >= 8
PY310 = sys.version_info[0] == 3 and sys.version_info[1] >= 10
PY311 = sys.version_info[0] == 3 and sys.version_info[1] >= 11


# Python 3.12 deprecates asyncio.iscoroutinefunction() as an alias for
# inspect.iscoroutinefunction(), whilst also removing the _is_coroutine marker.
# The latter is replaced with the inspect.markcoroutinefunction decorator.
# Until 3.12 is the minimum supported Python version, provide a shim.
# This was adapted from https://github.com/django/asgiref/blob/main/asgiref/sync.py
if hasattr(inspect, "markcoroutinefunction"):
    iscoroutinefunction = inspect.iscoroutinefunction
    markcoroutinefunction = inspect.markcoroutinefunction
else:
    iscoroutinefunction = asyncio.iscoroutinefunction

    def markcoroutinefunction(func):
        # type: (_F) -> _F
        # Prior to Python 3.12, asyncio exposed a private `_is_coroutine`
        # marker used by asyncio.iscoroutinefunction(). This attribute was
        # removed in Python 3.11. If it's not available, fall back to a no-op,
        # which preserves behavior of inspect.iscoroutinefunction for our
        # supported versions while avoiding AttributeError.
        try:
            marker = getattr(asyncio.coroutines, "_is_coroutine")
        except Exception:
            # No marker available on this Python version; return function as-is.
            return func

        try:  # pragma: no cover - defensive
            func._is_coroutine = marker  # type: ignore[attr-defined]
        except Exception:
            # If assignment fails for any reason, leave func unchanged.
            pass
        return func


def with_metaclass(meta, *bases):
    # type: (Any, *Any) -> Any
    class MetaClass(type):
        def __new__(metacls, name, this_bases, d):
            # type: (Any, Any, Any, Any) -> Any
            return meta(name, bases, d)

    return type.__new__(MetaClass, "temporary_class", (), {})


def check_uwsgi_thread_support():
    # type: () -> bool
    # We check two things here:
    #
    # 1. uWSGI doesn't run in threaded mode by default -- issue a warning if
    #    that's the case.
    #
    # 2. Additionally, if uWSGI is running in preforking mode (default), it needs
    #    the --py-call-uwsgi-fork-hooks option for the SDK to work properly. This
    #    is because any background threads spawned before the main process is
    #    forked are NOT CLEANED UP IN THE CHILDREN BY DEFAULT even if
    #    --enable-threads is on. One has to explicitly provide
    #    --py-call-uwsgi-fork-hooks to force uWSGI to run regular cpython
    #    after-fork hooks that take care of cleaning up stale thread data.
    try:
        from uwsgi import opt  # type: ignore
    except ImportError:
        return True

    from sentry_sdk.consts import FALSE_VALUES

    def enabled(option):
        # type: (str) -> bool
        value = opt.get(option, False)
        if isinstance(value, bool):
            return value

        if isinstance(value, bytes):
            try:
                value = value.decode()
            except Exception:
                pass

        return value and str(value).lower() not in FALSE_VALUES

    # When `threads` is passed in as a uwsgi option,
    # `enable-threads` is implied on.
    threads_enabled = "threads" in opt or enabled("enable-threads")
    fork_hooks_on = enabled("py-call-uwsgi-fork-hooks")
    lazy_mode = enabled("lazy-apps") or enabled("lazy")

    if lazy_mode and not threads_enabled:
        from warnings import warn

        warn(
            Warning(
                "IMPORTANT: "
                "We detected the use of uWSGI without thread support. "
                "This might lead to unexpected issues. "
                'Please run uWSGI with "--enable-threads" for full support.'
            )
        )

        return False

    elif not lazy_mode and (not threads_enabled or not fork_hooks_on):
        from warnings import warn

        warn(
            Warning(
                "IMPORTANT: "
                "We detected the use of uWSGI in preforking mode without "
                "thread support. This might lead to crashing workers. "
                'Please run uWSGI with both "--enable-threads" and '
                '"--py-call-uwsgi-fork-hooks" for full support.'
            )
        )

        return False

    return True
