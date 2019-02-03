from sentry_sdk.utils import add_global_repr_processor, safe_repr
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.events import iter_stacktraces

from sentry_sdk.integrations import Integration


class DeepVarsIntegration(Integration):
    identifier = "deep_vars"

    @staticmethod
    def setup_once():
        add_global_repr_processor(_deep_vars_repr)
        add_global_event_processor(_deep_vars_processor)


class DeepVar(object):
    __slots__ = ("obj", "fields")

    def __init__(self, obj, fields):
        self.obj = obj
        self.fields = fields


def _deep_vars_repr(obj, hint):
    if type(obj) in (list, dict, str, float, bool):
        return NotImplemented

    try:
        return DeepVar(
            obj, {k: safe_repr(obj.__dict__[k]) for k in dir(obj) if k in obj.__dict__}
        )
    except Exception:
        pass

    return NotImplemented


def _deep_vars_processor(event, hint):
    for stacktrace in iter_stacktraces(event):
        _process_stacktrace(stacktrace)

    return event


def _process_stacktrace(stacktrace):
    for frame in stacktrace.get("frames") or ():
        vars = frame.get("vars")
        if not vars:
            continue

        new_vars = {}

        for key, value in vars.items():
            if isinstance(value, DeepVar):
                for key2, value2 in value.fields.items():
                    new_vars["{}.{}".format(key, key2)] = value2
                new_vars[key] = safe_repr(value.obj)
            else:
                new_vars[key] = value

        frame["vars"] = new_vars
