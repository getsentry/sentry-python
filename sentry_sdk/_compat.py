import sys

MYPY = False
if MYPY:
    from typing import Optional
    from typing import Tuple
    from typing import Any
    from typing import Type


PY2 = sys.version_info[0] == 2

if PY2:
    import urlparse  # noqa

    text_type = unicode  # noqa
    import Queue as queue  # noqa

    string_types = (str, text_type)
    number_types = (int, long, float)  # noqa
    int_types = (int, long)  # noqa
    iteritems = lambda x: x.iteritems()  # noqa: B301

    def implements_str(cls):
        cls.__unicode__ = cls.__str__
        cls.__str__ = lambda x: unicode(x).encode("utf-8")  # noqa
        return cls

    exec("def reraise(tp, value, tb=None):\n raise tp, value, tb")


else:
    import urllib.parse as urlparse  # noqa
    import queue  # noqa

    text_type = str
    string_types = (text_type,)  # type: Tuple[type]
    number_types = (int, float)  # type: Tuple[type, type]
    int_types = (int,)  # noqa
    iteritems = lambda x: x.items()

    def _identity(x):
        return x

    def implements_str(x):
        return x

    def reraise(tp, value, tb=None):
        # type: (Optional[Type[BaseException]], Optional[BaseException], Optional[Any]) -> None
        assert value is not None
        if value.__traceback__ is not tb:
            raise value.with_traceback(tb)
        raise value


def with_metaclass(meta, *bases):
    class metaclass(type):
        def __new__(cls, name, this_bases, d):
            return meta(name, bases, d)

    return type.__new__(metaclass, "temporary_class", (), {})


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

    if str(opt.get("enable-threads", "0")).lower() in ("false", "off", "no", "0"):
        from warnings import warn

        warn(
            Warning(
                "We detected the use of uwsgi with disabled threads.  "
                "This will cause issues with the transport you are "
                "trying to use.  Please enable threading for uwsgi.  "
                '(Enable the "enable-threads" flag).'
            )
        )
