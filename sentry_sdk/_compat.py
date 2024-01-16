import sys

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import TypeVar

    T = TypeVar("T")


PY37 = sys.version_info[0] == 3 and sys.version_info[1] >= 7
PY310 = sys.version_info[0] == 3 and sys.version_info[1] >= 10
PY311 = sys.version_info[0] == 3 and sys.version_info[1] >= 11


def with_metaclass(meta, *bases):
    # type: (Any, *Any) -> Any
    class MetaClass(type):
        def __new__(metacls, name, this_bases, d):
            # type: (Any, Any, Any, Any) -> Any
            return meta(name, bases, d)

    return type.__new__(MetaClass, "temporary_class", (), {})


def check_thread_support():
    # type: () -> None
    try:
        from uwsgi import opt  # type: ignore
    except ImportError:
        return

    # When `threads` is passed in as a uwsgi option,
    # `enable-threads` is implied on.
    if "threads" in opt:
        return

    # put here because of circular import
    from sentry_sdk.consts import FALSE_VALUES

    if str(opt.get("enable-threads", "0")).lower() in FALSE_VALUES:
        from warnings import warn

        warn(
            Warning(
                "We detected the use of uwsgi with disabled threads.  "
                "This will cause issues with the transport you are "
                "trying to use.  Please enable threading for uwsgi.  "
                '(Add the "enable-threads" flag).'
            )
        )
