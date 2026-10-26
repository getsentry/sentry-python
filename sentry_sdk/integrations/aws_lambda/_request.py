import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from os import environ
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.data_collection import (
    _apply_data_collection_filtering_to_query_string,
    _apply_key_value_collection_filtering,
)
from sentry_sdk.integrations._wsgi_common import _filter_headers
from sentry_sdk.utils import AnnotatedValue

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Attributes, Event, EventProcessor, Hint


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


def _get_http_attributes(aws_event: "dict[str, Any]") -> "Attributes":
    """
    Span attributes of an API Gateway (REST or HTTP API), function URL or ALB request.
    https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html
    https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html
    https://docs.aws.amazon.com/elasticloadbalancing/latest/application/lambda-functions.html
    """
    data_collection = sentry_sdk.get_client().options["data_collection"]
    attributes: "Attributes" = {}

    headers = aws_event.get("headers")
    # some AWS services (e.g. EventBridge) send `headers` as a list or `None`.
    if not isinstance(headers, dict):
        headers = {}

    # REST APIs and ALB send every value of a repeated header in `multiValueHeaders`,
    # HTTP APIs and function URLs send cookies in `cookies`.
    header_values: "dict[str, Any]" = {name: [value] for name, value in headers.items()}
    header_values.update(aws_event.get("multiValueHeaders") or {})
    if aws_event.get("cookies"):
        header_values["cookie"] = ["; ".join(aws_event["cookies"])]
    for name, values in _filter_headers(header_values).items():
        attributes[f"{SPANDATA.HTTP_REQUEST_HEADER}.{name.lower()}"] = (
            values if isinstance(values, list) else [values]
        )

    request_context = aws_event.get("requestContext") or {}
    # HTTP APIs and function URLs.
    http = request_context.get("http") or {}

    method = aws_event.get("httpMethod") or http.get("method")
    if not method:
        return attributes
    attributes[SPANDATA.HTTP_REQUEST_METHOD] = method

    # the matched route, never the path: its IDs would make every request different.
    if aws_event.get("resource"):
        attributes[SPANDATA.HTTP_ROUTE] = aws_event["resource"]
    elif aws_event.get("routeKey") not in (None, "$default"):
        attributes[SPANDATA.HTTP_ROUTE] = aws_event["routeKey"].split(" ", 1)[-1]

    query = None
    url_query_params = data_collection["url_query_params"]
    if aws_event.get("rawQueryString"):
        query = _apply_data_collection_filtering_to_query_string(
            query_string=aws_event["rawQueryString"], behaviour=url_query_params
        )
    elif aws_event.get("multiValueQueryStringParameters"):
        query = urlencode(
            _apply_key_value_collection_filtering(
                items=aws_event["multiValueQueryStringParameters"],
                behaviour=url_query_params,
            ),
            doseq=True,
        )
    elif aws_event.get("queryStringParameters"):
        query = urlencode(
            _apply_key_value_collection_filtering(
                items=aws_event["queryStringParameters"], behaviour=url_query_params
            )
        )
    if query:
        attributes[SPANDATA.URL_QUERY] = query

    lowered_headers = {name.lower(): value for name, value in headers.items()}
    scheme = lowered_headers.get("x-forwarded-proto", "https")
    attributes[SPANDATA.URL_SCHEME] = scheme

    host = lowered_headers.get("host") or request_context.get("domainName")
    if host:
        attributes[SPANDATA.SERVER_ADDRESS] = host

    path = aws_event.get("rawPath") or aws_event.get("path")
    if path:
        attributes[SPANDATA.URL_PATH] = path
        if host:
            attributes[SPANDATA.URL_FULL] = f"{scheme}://{host}{path}" + (
                f"?{query}" if query else ""
            )

    identity = request_context.get("identity") or {}
    user_agent = lowered_headers.get("user-agent") or identity.get("userAgent")
    if user_agent:
        attributes[SPANDATA.USER_AGENT_ORIGINAL] = user_agent

    client_address = identity.get("sourceIp") or http.get("sourceIp")
    if client_address and data_collection["user_info"]:
        attributes[SPANDATA.CLIENT_ADDRESS] = client_address

    body = aws_event.get("body")
    if body and "incoming_request" in data_collection["http_bodies"]:
        if aws_event.get("isBase64Encoded"):
            try:
                body = base64.b64decode(body).decode("utf-8")
            except ValueError:
                # binary content.
                body = None
        if body:
            attributes[SPANDATA.HTTP_REQUEST_BODY_DATA] = body

    return attributes


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

        client_options = sentry_sdk.get_client().options
        data_collection = client_options["data_collection"]

        if "queryStringParameters" in aws_event:
            query_string = aws_event["queryStringParameters"]
            if query_string:
                filtered_qs = _apply_key_value_collection_filtering(
                    items=query_string,
                    behaviour=data_collection["url_query_params"],
                )
                if filtered_qs:
                    request["query_string"] = filtered_qs

        if "headers" in aws_event and isinstance(aws_event["headers"], dict):
            request["headers"] = _filter_headers(aws_event["headers"])

        if data_collection["user_info"]:
            extracted_user = _get_user_from_event(aws_event)
            if extracted_user:
                user_info = sentry_event.setdefault("user", {})
                for key, value in extracted_user.items():
                    user_info.setdefault(key, value)

        if "incoming_request" in data_collection["http_bodies"]:
            if "body" in aws_event:
                request["data"] = aws_event.get("body", "")
        elif aws_event.get("body", None):
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
        return f"{proto}://{host}{path}"
    return f"awslambda:///{aws_context.function_name}"


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
    if region.startswith("cn-"):
        domain = "amazonaws.cn"
    elif region.startswith("us-gov-"):
        domain = "amazonaws-us-gov.com"
    else:
        domain = "aws.amazon.com"

    url = (
        "https://console.{domain}/cloudwatch/home?region={region}"
        "#logEventViewer:group={log_group};stream={log_stream}"
        ";start={start_time};end={end_time}"
    ).format(
        domain=domain,
        region=region,
        log_group=aws_context.log_group_name,
        log_stream=aws_context.log_stream_name,
        start_time=(start_time - timedelta(seconds=1)).strftime(formatstring),
        end_time=(datetime.now(timezone.utc) + timedelta(seconds=2)).strftime(
            formatstring
        ),
    )

    return url
