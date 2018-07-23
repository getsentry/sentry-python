import io

def get_environ(environ):
    """
    Returns our whitelisted environment variables.
    """
    for key in ("REMOTE_ADDR", "SERVER_NAME", "SERVER_PORT"):
        if key in environ:
            yield key, environ[key]


def peek_io_stream(stream, input_peek_len=1000):
    if not hasattr(stream, 'read'):
        return None

    peek = stream.read(input_peek_len)
    while len(peek) < input_peek_len:
        x = stream.read(input_peek_len)
        if not x:
            break
        peek += x

    if isinstance(peek, bytes):
        peek_io = io.BytesIO(peek)
    else:
        peek_io = io.StringIO(peek)

    return peek, ChainedIO(peek_io, stream)


class ChainedIO(object):
    def __init__(self, *ios):
        self.ios = list(ios)
        self._last_type = bytes

    def read(self, n=None):
        if not self.ios:
            return self._last_type()

        rv = self.ios[0].read(n)

        if n is None or n < 0 or len(rv) < n:  # stream exhausted
            self.ios = self.ios[1:]  # jump to next stream

        self._last_type = type(rv)

        if n is None or n < 0:
            rv += self.read(n)
        return rv
