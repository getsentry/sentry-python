import sys


PY2 = sys.version_info[0] == 2

if PY2:
    import urlparse
    text_type = unicode
    import Queue as queue
    number_types = (int, long, float)

    def implements_str(cls):
        cls.__unicode__ = cls.__str__
        cls.__str__ = lambda x: unicode(x).encode('utf-8')
        return cls
else:
    import urllib.parse as urlparse
    import queue
    text_type = str
    implements_str = lambda x: x
    number_types = (int, float)


def with_metaclass(meta, *bases):
    class metaclass(type):
        def __new__(cls, name, this_bases, d):
            return meta(name, bases, d)
    return type.__new__(metaclass, 'temporary_class', (), {})
