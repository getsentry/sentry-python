from __future__ import absolute_import

from sentry_sdk import Hub
from sentry_sdk.hub import _should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.tracing import Span
from sentry_sdk.utils import capture_internal_exceptions

from sentry_sdk._types import MYPY

try:
    from pymongo import monitoring
except ImportError:
    raise DidNotEnable("Pymongo not installed")

if MYPY:
    from typing import Any, Dict, Union

    from pymongo.monitoring import (
        CommandFailedEvent,
        CommandStartedEvent,
        CommandSucceededEvent,
    )


class CommandTracer(monitoring.CommandListener):
    def __init__(self):
        # type: () -> None
        self._ongoing_operations = {}  # type: Dict[int, Span]

    def _operation_key(self, event):
        # type: (Union[CommandFailedEvent, CommandStartedEvent, CommandSucceededEvent]) -> int
        return event.request_id

    def started(self, event):
        # type: (CommandStartedEvent) -> None
        hub = Hub.current
        if hub.get_integration(PyMongoIntegration) is None:
            return
        with capture_internal_exceptions():
            command = dict(event.command)

            command.pop("$db", None)
            command.pop("$clusterTime", None)
            command.pop("$signature", None)

            op = "db.query"

            tags = {
                "db.name": event.database_name,
                "db.system": "mongodb",
                "db.operation": event.command_name,
            }

            try:
                tags["net.peer.name"] = event.connection_id[0]
                tags["net.peer.port"] = str(event.connection_id[1])
            except TypeError:
                pass

            data = {"operation_ids": {}}  # type: Dict[str, Dict[str, Any]]

            data["operation_ids"]["operation"] = event.operation_id
            data["operation_ids"]["request"] = event.request_id

            try:
                lsid = command.pop("lsid")["id"]
                data["operation_ids"]["session"] = str(lsid)
            except KeyError:
                pass

            if not _should_send_default_pii():
                for key in command:
                    command[key] = "%s"

            query = "{} {}".format(event.command_name, command)
            span = hub.start_span(op=op, description=query)

            for tag, value in tags.items():
                span.set_tag(tag, value)

            for key, value in data.items():
                span.set_data(key, value)

            with capture_internal_exceptions():
                hub.add_breadcrumb(message=query, category="query", type=op, data=tags)

            self._ongoing_operations[self._operation_key(event)] = span.__enter__()

    def failed(self, event):
        # type: (CommandFailedEvent) -> None
        hub = Hub.current
        if hub.get_integration(PyMongoIntegration) is None:
            return

        try:
            span = self._ongoing_operations.pop(self._operation_key(event))
            span.set_status("internal_error")
            span.__exit__(None, None, None)
        except KeyError:
            return

    def succeeded(self, event):
        # type: (CommandSucceededEvent) -> None
        hub = Hub.current
        if hub.get_integration(PyMongoIntegration) is None:
            return

        try:
            span = self._ongoing_operations.pop(self._operation_key(event))
            span.set_status("ok")
            span.__exit__(None, None, None)
        except KeyError:
            pass


class PyMongoIntegration(Integration):
    identifier = "pymongo"

    @staticmethod
    def setup_once():
        # type: () -> None
        monitoring.register(CommandTracer())
