import re

import sentry_sdk
from sentry_sdk._compat import PY2, iteritems
from sentry_sdk._types import MYPY
from sentry_sdk.tracing.consts import LOW_QUALITY_TRANSACTION_SOURCES
from sentry_sdk.utils import Dsn, capture_internal_exceptions


if PY2:
    from urllib import quote, unquote
else:
    from urllib.parse import quote, unquote


if MYPY:
    from typing import Dict, Optional

    from sentry_sdk.tracing.transaction import Transaction


class Baggage(object):
    __slots__ = ("sentry_items", "third_party_items", "mutable")

    SENTRY_PREFIX = "sentry-"
    SENTRY_PREFIX_REGEX = re.compile("^sentry-")

    # DynamicSamplingContext
    DSC_KEYS = [
        "trace_id",
        "public_key",
        "sample_rate",
        "release",
        "environment",
        "transaction",
        "user_id",
        "user_segment",
    ]

    def __init__(
        self,
        sentry_items,  # type: Dict[str, str]
        third_party_items="",  # type: str
        mutable=True,  # type: bool
    ):
        self.sentry_items = sentry_items
        self.third_party_items = third_party_items
        self.mutable = mutable

    @classmethod
    def from_incoming_header(cls, header):
        # type: (Optional[str]) -> Baggage
        """
        freeze if incoming header already has sentry baggage
        """
        sentry_items = {}
        third_party_items = ""
        mutable = True

        if header:
            for item in header.split(","):
                if "=" not in item:
                    continue

                with capture_internal_exceptions():
                    item = item.strip()
                    key, val = item.split("=")
                    if Baggage.SENTRY_PREFIX_REGEX.match(key):
                        baggage_key = unquote(key.split("-")[1])
                        sentry_items[baggage_key] = unquote(val)
                        mutable = False
                    else:
                        third_party_items += ("," if third_party_items else "") + item

        return Baggage(sentry_items, third_party_items, mutable)

    @classmethod
    def populate_from_transaction(cls, transaction):
        # type: (Transaction) -> Baggage
        """
        Populate fresh baggage entry with sentry_items and make it immutable
        if this is the head SDK which originates traces.
        """
        hub = transaction.hub or sentry_sdk.Hub.current
        client = hub.client
        sentry_items = {}  # type: Dict[str, str]

        if not client:
            return Baggage(sentry_items)

        options = client.options or {}
        user = (hub.scope and hub.scope._user) or {}

        sentry_items["trace_id"] = transaction.trace_id

        if options.get("environment"):
            sentry_items["environment"] = options["environment"]

        if options.get("release"):
            sentry_items["release"] = options["release"]

        if options.get("dsn"):
            sentry_items["public_key"] = Dsn(options["dsn"]).public_key

        if (
            transaction.name
            and transaction.source not in LOW_QUALITY_TRANSACTION_SOURCES
        ):
            sentry_items["transaction"] = transaction.name

        if user.get("segment"):
            sentry_items["user_segment"] = user["segment"]

        if transaction.sample_rate is not None:
            sentry_items["sample_rate"] = str(transaction.sample_rate)

        # there's an existing baggage but it was mutable,
        # which is why we are creating this new baggage.
        # However, if by chance the user put some sentry items in there, give them precedence.
        if transaction._baggage and transaction._baggage.sentry_items:
            sentry_items.update(transaction._baggage.sentry_items)

        return Baggage(sentry_items, mutable=False)

    def freeze(self):
        # type: () -> None
        self.mutable = False

    def dynamic_sampling_context(self):
        # type: () -> Dict[str, str]
        header = {}

        for key in Baggage.DSC_KEYS:
            item = self.sentry_items.get(key)
            if item:
                header[key] = item

        return header

    def serialize(self, include_third_party=False):
        # type: (bool) -> str
        items = []

        for key, val in iteritems(self.sentry_items):
            with capture_internal_exceptions():
                item = Baggage.SENTRY_PREFIX + quote(key) + "=" + quote(str(val))
                items.append(item)

        if include_third_party:
            items.append(self.third_party_items)

        return ",".join(items)
