import threading
import time
import hashlib
import struct
import uuid

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Dict
    from typing import Optional
    from typing import Any


MASK = 0xFFFFFFFF
FALLBACK_ID = uuid.uuid4()


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

    def next(self):
        # type: () -> float
        return self.next_u32() / MASK


class FeatureFlagsManager(object):
    def __init__(self, request_func=None, refresh=None, is_enabled=None):
        # type: (Any, Optional[float], Optional[bool]) -> None
        self._request_func = request_func
        self._fetched_flags = {}
        self._last_fetch = None
        self._dead = False
        self._refresh = refresh or 60.0
        self.is_enabled = is_enabled or False

        if self.is_enabled:

            def refresh():
                while self._dead:
                    if (
                        self._last_fetch is None
                        or self._last_fetch + self._refresh < time.time()
                    ):
                        self.refresh()
                    time.sleep(1.0)

            self._thread = threading.Thread(target=refresh)
            self._thread.daemon = True
            self._thread.start()

        self.refresh()

    def refresh(self):
        # type: () -> None
        if not self.is_enabled:
            return

        def callback(response):
            self._fetched_flags = response

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
                and roll_random_number(context) < eval_config["percentage"]
            ):
                return FeatureFlagInfo(
                    result=eval_config["result"],
                    payload=eval_config.get("payload") or {},
                    tags=tags,
                )

        return None

    def close(self):
        self._dead = True


class FeatureFlagInfo(object):
    def __init__(self, result, payload, tags):
        # type: (Any, Dict[str, Any], Dict[str, str]) -> None
        self.result = result
        self.payload = payload
        self.tags = tags


def roll_random_number(context):
    # type: (Dict[str, Any]) -> float
    sticky_id = context.get("stickyId") or context.get("userId") or FALLBACK_ID.hex
    return XorShift(seed=sticky_id).next()


def matches_tags(tags, context):
    # type: (Dict[str, str], Dict[str, Any]) -> None
    for key, value in tags.items():
        if context.get(key) != str(value):
            return False
    return True
