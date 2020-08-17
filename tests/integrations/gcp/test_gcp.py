"""
# GCP Cloud Functions system tests

"""
import json
import os
import time
from textwrap import dedent
import requests
import uuid
import tempfile
import shutil
import sys
import subprocess

import pytest
import pickle
import os.path
import os
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/cloud-platform.read-only",
    "https://www.googleapis.com/auth/cloudfunctions",
    "https://www.googleapis.com/auth/logging.read",
    "https://www.googleapis.com/auth/logging.admin",
]

FUNCTIONS_PRELUDE = """
import sentry_sdk
from sentry_sdk.integrations.gcp import GcpIntegration
import json
import time

from sentry_sdk.transport import HttpTransport

def event_processor(event):
    # Adding delay which would allow us to capture events.
    time.sleep(1)
    return event

class TestTransport(HttpTransport):
    def _send_event(self, event):
        event = event_processor(event)
        # Writing a single string to stdout holds the GIL (seems like) and
        # therefore cannot be interleaved with other threads. This is why we
        # explicitly add a newline at the end even though `print` would provide
        # us one.
        print("\\nEVENTS: {}\\n".format(json.dumps(event)))

def init_sdk(timeout_warning=False, **extra_init_args):
    sentry_sdk.init(
        dsn="https://123abc@example.com/123",
        transport=TestTransport,
        integrations=[GcpIntegration()],
        shutdown_timeout=10,
        **extra_init_args
    )
"""


@pytest.fixture
def authorized_credentials():
    credentials = None

    # Skipping tests if environment variables not set.
    if "SENTRY_PYTHON_TEST_GCP_CREDENTIALS_JSON" not in os.environ:
        pytest.skip("GCP environ vars not set")

    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    with open(
        os.environ.get("SENTRY_PYTHON_TEST_GCP_CREDENTIALS_JSON"), "rb"
    ) as creds_file:
        for line in creds_file.readlines():
            creds_json = json.loads(line)
    project_id = creds_json.get("installed", {}).get("project_id")
    if not project_id:
        pytest.skip("Credentials json file is not valid")

    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            credentials = pickle.load(token)
    # If there are no (valid) credentials available, let the user log in.
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            credential_json = os.environ.get("SENTRY_PYTHON_TEST_GCP_CREDENTIALS_JSON")
            flow = InstalledAppFlow.from_client_secrets_file(credential_json, SCOPES)
            credentials = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open("token.pickle", "wb") as token:
            pickle.dump(credentials, token)
    return credentials, project_id


@pytest.fixture(params=["python37"])
def functions_runtime(request):
    return request.param


@pytest.fixture
def run_cloud_function(request, authorized_credentials, functions_runtime):
    def inner(code, timeout="10s", subprocess_kwargs=()):

        events = []
        creds, project_id = authorized_credentials
        functions_service = build("cloudfunctions", "v1", credentials=creds)
        location_id = "us-central1"
        function_name = "test_function_{}".format(uuid.uuid4())
        name = "projects/{}/locations/{}/functions/{}".format(
            project_id, location_id, function_name
        )

        # STEP : Create a zip of cloud function

        subprocess_kwargs = dict(subprocess_kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            main_py = os.path.join(tmpdir, "main.py")
            with open(main_py, "w") as f:
                f.write(code)

            setup_cfg = os.path.join(tmpdir, "setup.cfg")

            with open(setup_cfg, "w") as f:
                f.write("[install]\nprefix=")

            subprocess.check_call(
                [sys.executable, "setup.py", "sdist", "-d", os.path.join(tmpdir, "..")],
                **subprocess_kwargs
            )

            subprocess.check_call(
                "pip install ../*.tar.gz -t .",
                cwd=tmpdir,
                shell=True,
                **subprocess_kwargs
            )
            shutil.make_archive(os.path.join(tmpdir, "ball"), "zip", tmpdir)

            # STEP : Generate a signed url
            parent = "projects/{}/locations/{}".format(project_id, location_id)

            api_request = (
                functions_service.projects()
                .locations()
                .functions()
                .generateUploadUrl(parent=parent)
            )
            upload_url_response = api_request.execute()

            upload_url = upload_url_response.get("uploadUrl")

            # STEP : Upload zip file of cloud function to generated signed url
            with open(os.path.join(tmpdir, "ball.zip"), "rb") as data:
                requests.put(
                    upload_url,
                    data=data,
                    headers={
                        "x-goog-content-length-range": "0,104857600",
                        "content-type": "application/zip",
                    },
                )

        # STEP : Create a new cloud function
        location = "projects/{}/locations/{}".format(project_id, location_id)

        function_url = "https://{}-{}.cloudfunctions.net/{}".format(
            location_id, project_id, function_name
        )

        body = {
            "name": name,
            "description": "Created as part of testsuite for getsentry/sentry-python",
            "entryPoint": "cloud_handler",
            "runtime": functions_runtime,
            "timeout": timeout,
            "availableMemoryMb": 128,
            "sourceUploadUrl": upload_url,
            "httpsTrigger": {"url": function_url},
        }

        api_request = (
            functions_service.projects()
            .locations()
            .functions()
            .create(location=location, body=body)
        )
        api_request.execute()

        # STEP : Invoke the cloud function
        # Adding delay of 60 seconds for new created function to get deployed.
        time.sleep(60)
        api_request = (
            functions_service.projects().locations().functions().call(name=name)
        )
        function_call_response = api_request.execute()

        # STEP : Fetch logs of invoked function
        log_name = "projects/{}/logs/cloudfunctions.googleapis.com%2Fcloud-functions".format(
            project_id
        )
        project_name = "projects/{}".format(project_id)
        body = {"resourceNames": [project_name], "filter": log_name}

        log_service = build("logging", "v2", credentials=creds)

        api_request = log_service.entries().list(body=body)
        log_response = api_request.execute()

        for entry in log_response.get("entries", []):
            entry_log_name = entry.get("logName")
            entry_function_name = (
                entry.get("resource", {}).get("labels", {}).get("function_name")
            )
            entry_text_payload = entry.get("textPayload", "")
            if (
                entry_log_name == log_name
                and entry_function_name == function_name
                and "EVENTS: " in entry_text_payload
            ):
                event = entry_text_payload[len("EVENTS: ") :]
                events.append(json.loads(event))

        log_flag = True

        # Looping so that appropriate event can be fetched from logs
        while log_response.get("nextPageToken") and log_flag:
            body = {
                "resourceNames": [project_name],
                "pageToken": log_response["nextPageToken"],
                "filter": log_name,
            }

            api_request = log_service.entries().list(body=body)
            log_response = api_request.execute()

            for entry in log_response.get("entries", []):
                entry_log_name = entry.get("logName")
                entry_function_name = (
                    entry.get("resource", {}).get("labels", {}).get("function_name")
                )
                entry_text_payload = entry.get("textPayload", "")
                if (
                    entry_log_name == log_name
                    and entry_function_name == function_name
                    and "EVENTS: " in entry_text_payload
                ):
                    log_flag = False
                    event = entry_text_payload[len("EVENTS: ") :]
                    events.append(json.loads(event))

        # STEP : Delete the cloud function
        @request.addfinalizer
        def delete_function():
            api_request = (
                functions_service.projects().locations().functions().delete(name=name)
            )
            api_request.execute()

        return events, function_call_response

    return inner


def test_handled_exception(run_cloud_function):
    events, response = run_cloud_function(
        FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk()


        def cloud_handler(request):
            raise Exception("something went wrong")
        """
        )
    )

    assert (
        response["error"]
        == "Error: function terminated. Recommended action: inspect logs for termination reason. Details:\nsomething went wrong"
    )
    (event,) = events
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"
    assert exception["mechanism"] == {"type": "gcp", "handled": False}


def test_initialization_order(run_cloud_function):
    events, response = run_cloud_function(
        FUNCTIONS_PRELUDE
        + dedent(
            """
        def cloud_handler(request):
            init_sdk()
            raise Exception("something went wrong")
        """
        )
    )

    assert (
        response["error"]
        == "Error: function terminated. Recommended action: inspect logs for termination reason. Details:\nsomething went wrong"
    )
    (event,) = events
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"
    assert exception["mechanism"] == {"type": "gcp", "handled": False}


def test_unhandled_exception(run_cloud_function):
    events, response = run_cloud_function(
        FUNCTIONS_PRELUDE
        + dedent(
            """
        init_sdk()


        def cloud_handler(request):
            x = 3/0
            return "str"
        """
        )
    )

    assert (
        response["error"]
        == "Error: function terminated. Recommended action: inspect logs for termination reason. Details:\ndivision by zero"
    )
    (event,) = events
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "Exception"
    assert exception["value"] == "something went wrong"
    assert exception["mechanism"] == {"type": "gcp", "handled": False}


def test_timeout_error(run_cloud_function):
    events, response = run_cloud_function(
        FUNCTIONS_PRELUDE
        + dedent(
            """
        def event_processor(event):
            return event

        init_sdk(timeout_warning=True)


        def cloud_handler(request):
            time.sleep(10)
            return "str"
        """,
            timeout=3,
        )
    )

    assert (
        response["error"]
        == "Error: function execution attempt timed out. Instance restarted."
    )
    (event,) = events
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]

    assert exception["type"] == "ServerlessTimeoutWarning"
    assert (
        exception["value"]
        == "WARNING : Function is expected to get timed out. Configured timeout duration = 3 seconds."
    )
    assert exception["mechanism"] == {"type": "threading", "handled": False}
