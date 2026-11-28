from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.serializer import serialize
from sentry_sdk.utils import _get_installed_modules

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, Optional, Tuple

    from sentry_sdk._types import Event

    _ModulesCacheEntry = Tuple[
        Dict[str, str],
        Optional[int],
        Optional[Callable[..., Optional[str]]],
        Optional[Dict[str, Any]],
    ]


class _SerializedModules(dict):  # type: ignore[type-arg]
    """Pre-serialized modules dict that returns a fresh plain dict to serialize()."""

    def __init__(
        self,
        data: "Dict[str, Any]",
        max_value_length: "Optional[int]" = None,
        custom_repr: "Optional[Callable[..., Optional[str]]]" = None,
    ) -> None:
        super().__init__(data)
        self._max_value_length = max_value_length
        self._custom_repr = custom_repr
        self._mutated = False

    def __sentry_repr__(self) -> "Dict[str, Any]":
        if self._mutated:
            return serialize(
                {"modules": dict(self)},
                max_value_length=self._max_value_length,
                custom_repr=self._custom_repr,
            )["modules"]
        return dict(self)

    def __setitem__(self, key: "Any", value: "Any") -> None:
        self._mutated = True
        super().__setitem__(key, value)

    def __delitem__(self, key: "Any") -> None:
        self._mutated = True
        super().__delitem__(key)

    def clear(self) -> None:
        self._mutated = True
        super().clear()

    def pop(self, *args: "Any") -> "Any":
        self._mutated = True
        return super().pop(*args)

    def popitem(self) -> "Tuple[Any, Any]":
        self._mutated = True
        return super().popitem()

    def setdefault(self, *args: "Any") -> "Any":
        self._mutated = True
        return super().setdefault(*args)

    def update(self, *args: "Any", **kwargs: "Any") -> None:
        self._mutated = True
        super().update(*args, **kwargs)


_cached_modules: "Optional[_ModulesCacheEntry]" = None


def _get_serialized_modules(
    raw_modules: "Dict[str, str]",
    max_value_length: "Optional[int]",
    custom_repr: "Optional[Callable[..., Optional[str]]]",
) -> "Dict[str, Any]":
    global _cached_modules
    cached = _cached_modules
    if (
        cached is None
        or cached[0] is not raw_modules
        or cached[1] != max_value_length
        or cached[2] != custom_repr
        or (cached[3] is not None and len(cached[3]) != len(raw_modules))
    ):
        serialized = serialize(
            {"modules": raw_modules},
            max_value_length=max_value_length,
            custom_repr=custom_repr,
        )
        pre_serialized: "Optional[Dict[str, Any]]" = (
            None if serialized.get("_meta") else serialized["modules"]
        )
        cached = (raw_modules, max_value_length, custom_repr, pre_serialized)
        _cached_modules = cached

    if cached[3] is None:
        return dict(raw_modules)

    return _SerializedModules(
        cached[3],
        max_value_length=max_value_length,
        custom_repr=custom_repr,
    )


class ModulesIntegration(Integration):
    identifier = "modules"

    @staticmethod
    def setup_once() -> None:
        @add_global_event_processor
        def processor(event: "Event", hint: "Any") -> "Event":
            if event.get("type") == "transaction":
                return event

            client = sentry_sdk.get_client()
            if client.get_integration(ModulesIntegration) is None:
                return event

            event["modules"] = _get_serialized_modules(
                _get_installed_modules(),
                client.options.get("max_value_length"),
                client.options.get("custom_repr"),
            )
            return event
