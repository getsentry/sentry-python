from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.redis.consts import (
    _COMMANDS_INCLUDING_SENSITIVE_DATA,
    _MAX_NUM_ARGS,
    _MULTI_KEY_COMMANDS,
    _SINGLE_KEY_COMMANDS,
)
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.utils import SENSITIVE_DATA_SUBSTITUTE, has_data_collection_enabled

if TYPE_CHECKING:
    from typing import Any, Optional, Sequence


def _get_safe_command(name: str, args: "Sequence[Any]") -> str:
    command_parts = [name]

    name_low = name.lower()
    send_default_pii = should_send_default_pii()
    client_options = sentry_sdk.get_client().options

    for i, arg in enumerate(args):
        if i > _MAX_NUM_ARGS:
            break

        if name_low in _COMMANDS_INCLUDING_SENSITIVE_DATA:
            command_parts.append(SENSITIVE_DATA_SUBSTITUTE)
            continue

        arg_is_the_key = i == 0
        if arg_is_the_key:
            command_parts.append(repr(arg))
        else:
            if has_data_collection_enabled(client_options):
                if client_options["data_collection"]["database_query_data"]:
                    command_parts.append(repr(arg))
            elif send_default_pii:
                command_parts.append(repr(arg))
            else:
                command_parts.append(SENSITIVE_DATA_SUBSTITUTE)

    command = " ".join(command_parts)
    return command


def _safe_decode(key: "Any") -> str:
    if isinstance(key, bytes):
        try:
            return key.decode()
        except UnicodeDecodeError:
            return ""

    return str(key)


def _key_as_string(key: "Any") -> str:
    if isinstance(key, (dict, list, tuple)):
        key = ", ".join(_safe_decode(x) for x in key)
    elif isinstance(key, bytes):
        key = _safe_decode(key)
    elif key is None:
        key = ""
    else:
        key = str(key)

    return key


def _get_safe_key(
    method_name: str,
    args: "Optional[tuple[Any, ...]]",
    kwargs: "Optional[dict[str, Any]]",
) -> "Optional[tuple[str, ...]]":
    """
    Gets the key (or keys) from the given method_name.
    The method_name could be a redis command or a django caching command
    """
    key = None

    if args is not None and method_name.lower() in _MULTI_KEY_COMMANDS:
        # for example redis "mget"
        key = tuple(args)

    elif args is not None and len(args) >= 1:
        # for example django "set_many/get_many" or redis "get"
        if isinstance(args[0], (dict, list, tuple)):
            key = tuple(args[0])
        else:
            key = (args[0],)

    elif kwargs is not None and "key" in kwargs:
        # this is a legacy case for older versions of Django
        if isinstance(kwargs["key"], (list, tuple)):
            if len(kwargs["key"]) > 0:
                key = tuple(kwargs["key"])
        else:
            if kwargs["key"] is not None:
                key = (kwargs["key"],)

    return key


def _parse_rediscluster_command(command: "Any") -> "Sequence[Any]":
    return command.args


def _set_client_data(
    span: "StreamedSpan", is_cluster: bool, name: str, *args: "Any"
) -> None:
    if name:
        span.set_attribute(SPANDATA.DB_OPERATION_NAME, name)

    key = _extract_key(name, args)
    if key is not None:
        span.set_attribute("db.redis.key", key)


def _extract_key(name: str, args: "Any") -> "Optional[str]":
    if not name or not args:
        return None

    name_low = name.lower()
    if (name_low in _SINGLE_KEY_COMMANDS) or (
        name_low in _MULTI_KEY_COMMANDS and len(args) == 1
    ):
        return args[0]

    return None
