from copy import deepcopy
from datetime import datetime, timedelta, timezone
from os import environ
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.data_collection import _apply_key_value_collection_filtering
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    AnnotatedValue,
    has_data_collection_enabled,
)

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Event, EventProcessor, Hint


def _get_user_from_event(aws_event: "dict[str, Any]") -> "dict[str, Any]":
    if not isinstance(aws_event, dict):
        return {}

    identity = aws_event.get("requestContext", {}).get("identity")
    if identity is None:
        return {}

    user_info: "dict[str, Any]" = {}

    user_arn = identity.get("userArn")
    if user_arn is not None:
        user_info["id"] = user_arn

    ip = identity.get("sourceIp")
    if ip is not None:
        user_info["ip_address"] = ip

    return user_info


def _make_request_event_processor(
    aws_event: "Any", aws_context: "Any", configured_timeout: "Any"
) -> "EventProcessor":
    start_time = datetime.now(timezone.utc)

    def event_processor(
        sentry_event: "Event", hint: "Hint", start_time: "datetime" = start_time
    ) -> "Optional[Event]":
        remaining_time_in_milis = aws_context.get_remaining_time_in_millis()
        exec_duration = configured_timeout - remaining_time_in_milis

        extra = sentry_event.setdefault("extra", {})
        extra["lambda"] = {
            "function_name": aws_context.function_name,
            "function_version": aws_context.function_version,
            "invoked_function_arn": aws_context.invoked_function_arn,
            "aws_request_id": aws_context.aws_request_id,
            "execution_duration_in_millis": exec_duration,
            "remaining_time_in_millis": remaining_time_in_milis,
        }

        extra["cloudwatch logs"] = {
            "url": _get_cloudwatch_logs_url(aws_context, start_time),
            "log_group": aws_context.log_group_name,
            "log_stream": aws_context.log_stream_name,
        }

        request = sentry_event.get("request", {})

        if "httpMethod" in aws_event:
            request["method"] = aws_event["httpMethod"]

        request["url"] = _get_url(aws_event, aws_context)

        if "queryStringParameters" in aws_event:
            query_string = aws_event["queryStringParameters"]
            client_options = sentry_sdk.get_client().options
            if has_data_collection_enabled(client_options):
                if query_string:
                    filtered_qs = _apply_key_value_collection_filtering(
                        items=query_string,
                        behaviour=client_options["data_collection"]["url_query_params"],
                    )
                    if filtered_qs:
                        request["query_string"] = filtered_qs
            else:
                request["query_string"] = query_string

        if "headers" in aws_event and isinstance(aws_event["headers"], dict):
            request["headers"] = _filter_headers(aws_event["headers"])

        client_options = sentry_sdk.get_client().options
        if has_data_collection_enabled(client_options):
            if client_options["data_collection"]["user_info"]:
                extracted_user = _get_user_from_event(aws_event)
                if extracted_user:
                    user_info = sentry_event.setdefault("user", {})
                    for key, value in extracted_user.items():
                        user_info.setdefault(key, value)

            if "incoming_request" in client_options["data_collection"]["http_bodies"]:
                if "body" in aws_event:
                    request["data"] = aws_event.get("body", "")

        elif should_send_default_pii():
            extracted_user = _get_user_from_event(aws_event)
            if extracted_user:
                user_info = sentry_event.setdefault("user", {})
                for key, value in extracted_user.items():
                    user_info.setdefault(key, value)

            if "body" in aws_event:
                request["data"] = aws_event.get("body", "")
        else:
            if aws_event.get("body", None):
                # Unfortunately couldn't find a way to get structured body from AWS
                # event. Meaning every body is unstructured to us.
                request["data"] = AnnotatedValue.removed_because_raw_data()

        sentry_event["request"] = deepcopy(request)

        return sentry_event

    return event_processor


def _get_url(aws_event: "Any", aws_context: "Any") -> str:
    path = aws_event.get("path", None)

    headers = aws_event.get("headers")
    if not isinstance(headers, dict):
        headers = {}

    host = headers.get("Host", None)
    proto = headers.get("X-Forwarded-Proto", None)
    if proto and host and path:
        return "{}://{}{}".format(proto, host, path)
    return "awslambda:///{}".format(aws_context.function_name)


def _get_cloudwatch_logs_url(aws_context: "Any", start_time: "datetime") -> str:
    """
    Generates a CloudWatchLogs console URL based on the context object

    Arguments:
        aws_context {Any} -- context from lambda handler

    Returns:
        str -- AWS Console URL to logs.
    """
    formatstring = "%Y-%m-%dT%H:%M:%SZ"
    region = environ.get("AWS_REGION", "")

    url = (
        "https://console.{domain}/cloudwatch/home?region={region}"
        "#logEventViewer:group={log_group};stream={log_stream}"
        ";start={start_time};end={end_time}"
    ).format(
        domain="amazonaws.cn" if region.startswith("cn-") else "aws.amazon.com",
        region=region,
        log_group=aws_context.log_group_name,
        log_stream=aws_context.log_stream_name,
        start_time=(start_time - timedelta(seconds=1)).strftime(formatstring),
        end_time=(datetime.now(timezone.utc) + timedelta(seconds=2)).strftime(
            formatstring
        ),
    )

    return url
