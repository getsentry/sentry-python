from __future__ import absolute_import

from sentry_sdk import Hub
from sentry_sdk.utils import capture_internal_exceptions
from sentry_sdk.integrations import Integration


class RedisIntegration(Integration):
    identifier = "redis"

    @staticmethod
    def setup_once():
        import redis

        old_execute_command = redis.StrictRedis.execute_command

        def sentry_patched_execute_command(self, name, *args, **kwargs):
            hub = Hub.current

            if hub.get_integration(RedisIntegration) is None:
                return old_execute_command(self, name, *args, **kwargs)

            description = name

            with capture_internal_exceptions():
                description_parts = [name]
                for i, arg in enumerate(args):
                    if i > 10:
                        break

                    description_parts.append(repr(arg))

                description = " ".join(description_parts)

            with hub.span(op="redis", description=description) as span:
                if name and args and name.lower() in ("get", "set", "setex", "setnx"):
                    span.set_tag("redis.key", args[0])

                return old_execute_command(self, name, *args, **kwargs)

        redis.StrictRedis.execute_command = (  # type: ignore
            sentry_patched_execute_command
        )
