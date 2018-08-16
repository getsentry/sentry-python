import sys


PY2 = sys.version_info[0] == 2

if PY2:
    import urlparse  # noqa

    text_type = unicode  # noqa
    import Queue as queue  # noqa

    number_types = (int, long, float)  # noqa

    def implements_str(cls):
        cls.__unicode__ = cls.__str__
        cls.__str__ = lambda x: unicode(x).encode("utf-8")  # noqa
        return cls

    exec("def reraise(tp, value, tb=None):\n raise tp, value, tb")

    def implements_iterator(cls):
        cls.next = cls.__next__
        del cls.__next__
        return cls


else:
    import urllib.parse as urlparse  # noqa
    import queue  # noqa

    text_type = str
    number_types = (int, float)

    def _identity(x):
        return x

    implements_iterator = _identity

    def implements_str(x):
        return x

    def reraise(tp, value, tb=None):
        if value.__traceback__ is not tb:
            raise value.with_traceback(tb)
        raise value


def with_metaclass(meta, *bases):
    class metaclass(type):
        def __new__(cls, name, this_bases, d):
            return meta(name, bases, d)

    return type.__new__(metaclass, "temporary_class", (), {})
