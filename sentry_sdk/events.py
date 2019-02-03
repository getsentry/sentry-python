def _filter_none(value):
    if value is not None:
        yield value


def _iter_values(values):
    if isinstance(values, list):
        return values
    elif isinstance(values, dict):
        return values.get("values") or ()
    else:
        return ()


def iter_stacktraces(event):
    x = event.get("stacktrace")
    if x is not None:
        yield x

    exception_values = event.get("exception")
    if exception_values is not None:
        for exception in _iter_values(exception_values):
            x = exception.get("stacktrace")
            if x is not None:
                yield x

    thread_values = event.get("threads")
    if thread_values is not None:
        for exception in _iter_values(thread_values):
            x = exception.get("stacktrace")
            if x is not None:
                yield x
