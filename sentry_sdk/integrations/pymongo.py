import copy
import json

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import SpanStatus, StreamedSpan
from sentry_sdk.tracing import Span
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import capture_internal_exceptions

try:
    from pymongo import monitoring
except ImportError:
    raise DidNotEnable("Pymongo not installed")

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

    client = sentry_sdk.get_client()
    is_span_streaming_enabled = has_span_streaming_enabled(client.options)

    data[SPANDATA.DB_DRIVER_NAME] = "pymongo"
    db_name = event.database_name

    server_address = event.connection_id[0]
    if server_address is not None:
        data[SPANDATA.SERVER_ADDRESS] = server_address

    server_port = event.connection_id[1]
    if server_port is not None:
        data[SPANDATA.SERVER_PORT] = server_port

    if is_span_streaming_enabled:
        data["db.system.name"] = "mongodb"

        if db_name is not None:
            data["db.namespace"] = db_name
    else:
        data[SPANDATA.DB_SYSTEM] = "mongodb"

        if db_name is not None:
            data[SPANDATA.DB_NAME] = db_name

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
            db_name = event.database_name

            lsid = command.pop("lsid", None)
            if not should_send_default_pii():
                command = _strip_pii(command)

            query = json.dumps(command, default=str)

            if has_span_streaming_enabled(client.options):
                span_first_data = {
                    "db.operation.name": operation_name,
                    "db.collection.name": collection_name,
                    "sentry.op": OP.DB,
                    "sentry.origin": PyMongoIntegration.origin,
                    **db_data,
                }

                span = sentry_sdk.traces.start_span(
                    name=query, attributes=span_first_data
                )

                with capture_internal_exceptions():
                    sentry_sdk.add_breadcrumb(
                        message=query,
                        category="query",
                        type=OP.DB,
                        data=span_first_data,
                    )

            else:
                tags = {
                    "db.name": db_name,
                    SPANDATA.DB_SYSTEM: "mongodb",
                    SPANDATA.DB_DRIVER_NAME: "pymongo",
                    SPANDATA.DB_OPERATION: operation_name,
                    # The below is a deprecated field, but leaving for legacy reasons.
                    # The v2 spans will use `db.collection.name` instead.
                    SPANDATA.DB_MONGODB_COLLECTION: collection_name,
                }

                try:
                    tags["net.peer.name"] = event.connection_id[0]
                    tags["net.peer.port"] = str(event.connection_id[1])
                except TypeError:
                    pass

                data: "Dict[str, Any]" = {"operation_ids": {}}
                data["operation_ids"]["operation"] = event.operation_id
                data["operation_ids"]["request"] = event.request_id

                data.update(db_data)

                try:
                    if lsid:
                        lsid_id = lsid["id"]
                        data["operation_ids"]["session"] = str(lsid_id)
                except KeyError:
                    pass

                span = sentry_sdk.start_span(
                    op=OP.DB,
                    name=query,
                    origin=PyMongoIntegration.origin,
                )

                for tag, value in tags.items():
                    # set the tag for backwards-compatibility.
                    # TODO: remove the set_tag call in the next major release!
                    span.set_tag(tag, value)
                    span.set_data(tag, value)

                for key, value in data.items():
                    span.set_data(key, value)

                with capture_internal_exceptions():
                    sentry_sdk.add_breadcrumb(
                        message=query, category="query", type=OP.DB, data=tags
                    )

            self._ongoing_operations[self._operation_key(event)] = span.__enter__()

    def failed(self, event: "CommandFailedEvent") -> None:
        if sentry_sdk.get_client().get_integration(PyMongoIntegration) is None:
            return

        try:
            span = self._ongoing_operations.pop(self._operation_key(event))
            # Ignoring NoOpStreamedSpan as it will always have a status of "ok"
            if type(span) is StreamedSpan:
                span.status = SpanStatus.ERROR
            elif type(span) is Span:
                span.set_status(SPANSTATUS.INTERNAL_ERROR)
            span.__exit__(None, None, None)
        except KeyError:
            return

    def succeeded(self, event: "CommandSucceededEvent") -> None:
        if sentry_sdk.get_client().get_integration(PyMongoIntegration) is None:
            return

        try:
            span = self._ongoing_operations.pop(self._operation_key(event))
            if type(span) is Span:
                span.set_status(SPANSTATUS.OK)
            span.__exit__(None, None, None)
        except KeyError:
            pass


class PyMongoIntegration(Integration):
    identifier = "pymongo"
    origin = f"auto.db.{identifier}"

    @staticmethod
    def setup_once() -> None:
        monitoring.register(CommandTracer())
