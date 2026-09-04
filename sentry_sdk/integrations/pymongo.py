import copy
import json

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import SpanStatus, StreamedSpan
from sentry_sdk.tracing import Span
from sentry_sdk.utils import (
    capture_internal_exceptions,
    has_data_collection_enabled,
    parse_version,
)

try:
    from pymongo import __version__ as PYMONGO_VERSION
    from pymongo import monitoring
except ImportError:
    raise DidNotEnable("Pymongo not installed or incompatible")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Union

    from pymongo.monitoring import (
        CommandFailedEvent,
        CommandStartedEvent,
        CommandSucceededEvent,
    )


SAFE_COMMAND_ATTRIBUTES = [
    "insert",
    "ordered",
    "find",
    "limit",
    "singleBatch",
    "aggregate",
    "createIndexes",
    "indexes",
    "delete",
    "findAndModify",
    "renameCollection",
    "to",
    "drop",
]


def _strip_pii(command: "Dict[str, Any]") -> "Dict[str, Any]":
    for key in command:
        is_safe_field = key in SAFE_COMMAND_ATTRIBUTES
        if is_safe_field:
            # Skip if safe key
            continue

        update_db_command = key == "update" and "findAndModify" not in command
        if update_db_command:
            # Also skip "update" db command because it is save.
            # There is also an "update" key in the "findAndModify" command, which is NOT safe!
            continue

        # Special stripping for documents
        is_document = key == "documents"
        if is_document:
            for doc in command[key]:
                for doc_key in doc:
                    doc[doc_key] = "%s"
            continue

        # Special stripping for dict style fields
        is_dict_field = key in ["filter", "query", "update"]
        if is_dict_field:
            for item_key in command[key]:
                command[key][item_key] = "%s"
            continue

        # For pipeline fields strip the `$match` dict
        is_pipeline_field = key == "pipeline"
        if is_pipeline_field:
            for pipeline in command[key]:
                for match_key in pipeline["$match"] if "$match" in pipeline else []:
                    pipeline["$match"][match_key] = "%s"
            continue

        # Default stripping
        command[key] = "%s"

    return command


def _get_db_data(event: "Any") -> "Dict[str, Any]":
    data = {}

    data[SPANDATA.DB_DRIVER_NAME] = "pymongo"
    db_name = event.database_name

    server_address = event.connection_id[0]
    if server_address is not None:
        data[SPANDATA.SERVER_ADDRESS] = server_address

    server_port = event.connection_id[1]
    if server_port is not None:
        data[SPANDATA.SERVER_PORT] = server_port

    data["db.system.name"] = "mongodb"

    if db_name is not None:
        data["db.namespace"] = db_name

    return data


class CommandTracer(monitoring.CommandListener):
    def __init__(self) -> None:
        self._ongoing_operations: "Dict[int, Union[Span, StreamedSpan]]" = {}

    def _operation_key(
        self,
        event: "Union[CommandFailedEvent, CommandStartedEvent, CommandSucceededEvent]",
    ) -> int:
        return event.request_id

    def started(self, event: "CommandStartedEvent") -> None:
        client = sentry_sdk.get_client()
        if client.get_integration(PyMongoIntegration) is None:
            return

        with capture_internal_exceptions():
            command = dict(copy.deepcopy(event.command))

            command.pop("$db", None)
            command.pop("$clusterTime", None)
            command.pop("$signature", None)

            db_data = _get_db_data(event)

            collection_name = command.get(event.command_name)
            operation_name = event.command_name

            if has_data_collection_enabled(client.options):
                if not client.options["data_collection"]["database_query_data"]:
                    command = _strip_pii(command)
            elif not should_send_default_pii():
                command = _strip_pii(command)

            query = json.dumps(command, default=str)

            data = {
                "db.operation.name": operation_name,
                "db.collection.name": collection_name,
                SPANDATA.DB_QUERY_TEXT: query,
                "sentry.op": OP.DB,
                "sentry.origin": PyMongoIntegration.origin,
                **db_data,
            }

            with capture_internal_exceptions():
                sentry_sdk.add_breadcrumb(
                    message=query,
                    category="query",
                    type=OP.DB,
                    data=data,
                )

            if sentry_sdk.traces.get_current_span() is None:
                return

            span = sentry_sdk.traces.start_span(name=query, attributes=data)

            self._ongoing_operations[self._operation_key(event)] = span

    def failed(self, event: "CommandFailedEvent") -> None:
        if sentry_sdk.get_client().get_integration(PyMongoIntegration) is None:
            return

        try:
            span = self._ongoing_operations.pop(self._operation_key(event))
            span.status = SpanStatus.ERROR
            span.end()
        except KeyError:
            return

    def succeeded(self, event: "CommandSucceededEvent") -> None:
        if sentry_sdk.get_client().get_integration(PyMongoIntegration) is None:
            return

        try:
            span = self._ongoing_operations.pop(self._operation_key(event))
            span.end()
        except KeyError:
            pass


class PyMongoIntegration(Integration):
    identifier = "pymongo"
    origin = f"auto.db.{identifier}"

    @staticmethod
    def setup_once() -> None:
        version = parse_version(PYMONGO_VERSION)
        _check_minimum_version(PyMongoIntegration, version)

        monitoring.register(CommandTracer())
