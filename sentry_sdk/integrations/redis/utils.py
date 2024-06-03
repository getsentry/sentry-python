from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.redis.consts import (
    _COMMANDS_INCLUDING_SENSITIVE_DATA,
    _MAX_NUM_ARGS,
    _MAX_NUM_COMMANDS,
    _MULTI_KEY_COMMANDS,
    _SINGLE_KEY_COMMANDS,
)
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import SENSITIVE_DATA_SUBSTITUTE


if TYPE_CHECKING:
    from typing import Any, Optional, Sequence
    from sentry_sdk.tracing import Span


def _get_safe_command(name, args):
    # type: (str, Sequence[Any]) -> str
    command_parts = [name]

    for i, arg in enumerate(args):
        if i > _MAX_NUM_ARGS:
            break

        name_low = name.lower()

        if name_low in _COMMANDS_INCLUDING_SENSITIVE_DATA:
            command_parts.append(SENSITIVE_DATA_SUBSTITUTE)
            continue

        arg_is_the_key = i == 0
        if arg_is_the_key:
            command_parts.append(repr(arg))

        else:
            if should_send_default_pii():
                command_parts.append(repr(arg))
            else:
                command_parts.append(SENSITIVE_DATA_SUBSTITUTE)

    command = " ".join(command_parts)
    return command


def _get_safe_key(method_name, args, kwargs):
    # type: (str, Optional[tuple[Any, ...]], Optional[dict[str, Any]]) -> str
    """
    Gets the keys (or keys) from the given method_name.
    The method_name could be a redis command or a django caching command
    """
    key = ""
    if args is not None and method_name.lower() in _MULTI_KEY_COMMANDS:
        # for example redis "mget"
        key = ", ".join(x.decode() if isinstance(x, bytes) else x for x in args)

    elif args is not None and len(args) >= 1:
        # for example django "set_many/get_many" or redis "get"
        key = args[0].decode() if isinstance(args[0], bytes) else args[0]

    elif kwargs is not None and "key" in kwargs:
        # this is a legacy case for older versions of django (I guess)
        key = (
            kwargs["key"].decode()
            if isinstance(kwargs["key"], bytes)
            else kwargs["key"]
        )

    if isinstance(key, dict):
        # Django caching set_many() has a dictionary {"key": "data", "key2": "data2"}
        # as argument. In this case only return the keys of the dictionary (to not leak data)
        key = ", ".join(x.decode() if isinstance(x, bytes) else x for x in key.keys())

    if isinstance(key, list):
        key = ", ".join(x.decode() if isinstance(x, bytes) else x for x in key)

    return str(key)


def _parse_rediscluster_command(command):
    # type: (Any) -> Sequence[Any]
    return command.args


def _set_pipeline_data(
    span, is_cluster, get_command_args_fn, is_transaction, command_stack
):
    # type: (Span, bool, Any, bool, Sequence[Any]) -> None
    span.set_tag("redis.is_cluster", is_cluster)
    span.set_tag("redis.transaction", is_transaction)

    commands = []
    for i, arg in enumerate(command_stack):
        if i >= _MAX_NUM_COMMANDS:
            break

        command = get_command_args_fn(arg)
        commands.append(_get_safe_command(command[0], command[1:]))

    span.set_data(
        "redis.commands",
        {
            "count": len(command_stack),
            "first_ten": commands,
        },
    )


def _set_client_data(span, is_cluster, name, *args):
    # type: (Span, bool, str, *Any) -> None
    span.set_tag("redis.is_cluster", is_cluster)
    if name:
        span.set_tag("redis.command", name)
        span.set_tag(SPANDATA.DB_OPERATION, name)

    if name and args:
        name_low = name.lower()
        if (name_low in _SINGLE_KEY_COMMANDS) or (
            name_low in _MULTI_KEY_COMMANDS and len(args) == 1
        ):
            span.set_tag("redis.key", args[0])
