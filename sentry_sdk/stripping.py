from collections import Mapping, Sequence

from ._compat import text_type

def strip_event(event):
    assert "" not in event, "Merging metadata not supported"
    old_frames = event.get('stacktrace', {}).get('frames', [])
    new_frames = []
    frames_meta = {}
    meta = {"stacktrace": {"frames": frames_meta}}

    for i, frame in old_frames:
        frame_meta, new_frame = strip_frame(frame)
        new_frames.append(new_frame)
        if frame_meta is not None:
            frames_meta[str(i)] = frame_meta

    return event, meta

def strip_frame(frame):
    frame['vars'], meta = strip_databag(frame.get('vars'))
    return frame, ({"vars": meta} if meta is not None else None)

def strip_databag(obj, remaining_depth=20):
    if remaining_depth <= 0:
        return None, {"": {"rem": [["!dep", "x"]]}}
    if isinstance(obj, text_type):
        return strip_string(obj)
    if isinstance(obj, Mapping):
        rv = {}
        meta = {}
        for k, v in obj.items():
            rv[k], v_meta = strip_databag(v, remaining_depth - 1)
            if v_meta is not None:
                meta[k] = v_meta
        return rv, meta
    if isinstance(obj, Sequence):
        rv = []
        meta = {}
        for i, v in enumerate(obj):
            new_v, v_meta = strip_databag(obj, remaining_depth - 1)
            rv.append(new_v)
            if v_meta is not None:
                meta[str(i)] = v_meta
        return rv, meta

    return obj, None


def strip_string(value, length=512):
    if not value:
        return value, None
    if len(value) > length:
        meta = {
            "": {
                "len": len(value),
                "rem": [["!len", "x", length, len(value)]]
            }
        }
        return value[:length - 3] + '...', meta
    return value[:length], None
