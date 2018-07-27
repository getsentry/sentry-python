from collections import Mapping, Sequence

from ._compat import text_type


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
                new_v, meta[i] = inner(v)
                rv.append(new_v)
                if meta[i] is None:
                    del meta[i]
            return rv, (meta or None)
        if isinstance(obj, AnnotatedValue):
            return obj.value, {"": obj.metadata}
        return obj, None

    obj, meta = inner(obj)
    if meta is not None:
        obj[""] = meta
    return obj


def strip_event(event):
    old_frames = event.get("stacktrace", {}).get("frames", None)
    if old_frames:
        event["stacktrace"]["frames"] = [strip_frame(frame) for frame in old_frames]

    old_request_data = event.get("request", {}).get("data", None)
    if old_request_data:
        event["request"]["data"] = strip_databag(old_request_data)

    return event


def strip_frame(frame):
    frame["vars"], meta = strip_databag(frame.get("vars"))
    return frame, ({"vars": meta} if meta is not None else None)


def strip_databag(obj, remaining_depth=20):
    assert not isinstance(obj, bytes), "bytes should have been normalized before"
    if remaining_depth <= 0:
        return AnnotatedValue(None, {"rem": [["!dep", "x"]]})
    if isinstance(obj, text_type):
        return strip_string(obj)
    if isinstance(obj, Mapping):
        return {k: strip_databag(v, remaining_depth - 1) for k, v in obj.items()}
    if isinstance(obj, Sequence):
        return [strip_databag(v, remaining_depth - 1) for v in obj]
    return obj


def strip_string(value, assume_length=None, max_length=512):
    # TODO: read max_length from config
    if not value:
        return value
    if assume_length is None:
        assume_length = len(value)

    if assume_length > max_length:
        return AnnotatedValue(
            value=value[: max_length - 3] + u"...",
            metadata={
                "len": assume_length,
                "rem": [["!len", "x", max_length - 3, max_length]],
            },
        )
    return value[:max_length]
