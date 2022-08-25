import threading
import time
import hashlib
import struct
import uuid

from collections import namedtuple

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Dict
    from typing import Optional
    from typing import Any


MASK = 0xFFFFFFFF
FALLBACK_ID = uuid.getnode()


class XorShift(object):
    def __init__(self, seed=None):
        # type: (Optional[Any]) -> None
        self.state = [0] * 4
        if seed is not None:
            self.seed(seed)

    def seed(self, seed):
        # type: (Any) -> None
        bytes = hashlib.sha1(seed.encode("utf-8")).digest()
        # 4 big endian integers, eg:
        #   (x[0] << 24) | (x[1] << 16) | (x[2] << 8) | (x[3])
        self.state[:] = struct.unpack(">IIII", bytes[:16])

    def next_u32(self):
        # type: () -> int
        t = self.state[3]
        s = self.state[0]

        self.state[3] = self.state[2]
        self.state[2] = self.state[1]
        self.state[1] = s

        t = (t << 11) & MASK
        t ^= t >> 8
        self.state[0] = (t ^ s ^ (s >> 19)) & MASK

        return self.state[0]

    def next_float(self):
        # type: () -> float
        return self.next_u32() / MASK


class FeatureFlagsManager(object):
    def __init__(self, request_func, refresh=None, is_enabled=None):
        # type: (Any, Optional[float], Optional[bool]) -> None
        self._request_func = request_func
        self._fetched_flags = {}  # type: Dict[str, Any]
        self._last_fetch = None  # type: Optional[float]
        self._dead = False  # type: bool
        self._refresh = refresh or 60.0
        self.is_enabled = is_enabled or False
        self._load_event = threading.Event()  # type: threading.Event

        if self.is_enabled:

            def thread_func():
                # type: () -> None

                while self._dead:
                    if (
                        self._last_fetch is None
                        or self._last_fetch + self._refresh < time.time()
                    ):
                        self.refresh()
                    time.sleep(1.0)

            self._thread = threading.Thread(target=thread_func)
            self._thread.daemon = True
            self._thread.start()

        self.refresh()

    def wait(self, timeout=None):
        # type: (Optional[float]) -> bool
        if not self.is_enabled:
            return True
        return self._load_event.wait(timeout)

    def refresh(self):
        # type: () -> None
        if not self.is_enabled:
            return

        def callback(response):
            # type: (Any) -> None
            self._fetched_flags = response
            self._load_event.set()

        self._request_func(callback)
        self._last_fetch = time.time()

    def evaluate_feature_flag(self, name, context):
        # type: (str, Dict[str, Any]) -> Optional[FeatureFlagInfo]
        config = self._fetched_flags.get(name)
        if config is None:
            return None

        for eval_config in config["evaluation"]:
            tags = eval_config.get("tags") or {}
            if not matches_tags(tags, context):
                continue
            if eval_config["type"] == "match" or (
                eval_config["type"] == "rollout"
                and roll_random_number(config.get("group") or name, context)
                < eval_config["percentage"]
            ):
                return FeatureFlagInfo(
                    result=eval_config["result"],
                    payload=eval_config.get("payload") or {},
                    tags=tags,
                )

        return None

    def close(self):
        # type: () -> None
        self._dead = True


FeatureFlagInfo = namedtuple("FeatureFlagInfo", ["result", "payload", "tags"])


def roll_random_number(group, context):
    # type: (str, Dict[str, Any]) -> float
    sticky_id = str(context.get("stickyId") or context.get("userId") or FALLBACK_ID)
    seed = "%s|%s" % (group, sticky_id)
    return XorShift(seed=sticky_id).next_float()


def matches_tags(tags, context):
    # type: (Dict[str, str], Dict[str, Any]) -> bool
    for key, value in tags.items():
        context_value = context.get(key)
        if isinstance(context_value, list):
            if str(value) not in context_value:
                return False
        elif context_value != str(value):
            return False
    return True
