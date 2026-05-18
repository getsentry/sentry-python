import os
import platform
import subprocess
import sys
from collections.abc import Mapping

import pytest

import sentry_sdk
from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.stdlib import StdlibIntegration
from tests.conftest import ApproxDict


class ImmutableDict(Mapping):
    def __init__(self, inner):
        self.inner = inner

    def __getitem__(self, key):
        return self.inner[key]

    def __iter__(self):
        return iter(self.inner)

    def __len__(self):
        return len(self.inner)


@pytest.mark.parametrize("positional_args", [True, False])
@pytest.mark.parametrize(
    "iterator",
    [
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                platform.python_implementation() == "PyPy",
                reason="https://bitbucket.org/pypy/pypy/issues/3050/subprocesspopen-only-accepts-sequences",
            ),
        ),
        False,
    ],
    ids=("as_iterator", "as_list"),
)
@pytest.mark.parametrize("env_mapping", [None, os.environ, ImmutableDict(os.environ)])
@pytest.mark.parametrize("with_cwd", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
def test_subprocess_basic(
    sentry_init,
    capture_events,
    capture_items,
    monkeypatch,
    positional_args,
    iterator,
    env_mapping,
    with_cwd,
    span_streaming,
):
    monkeypatch.setenv("FOO", "bar")

    old_environ = dict(os.environ)

    sentry_init(
        integrations=[StdlibIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    if span_streaming:
        items = capture_items("event", "span")

        with sentry_sdk.traces.start_span(name="custom parent") as span:
            args = [
                sys.executable,
                "-c",
                "import os; "
                "import sentry_sdk; "
                "from sentry_sdk.integrations.stdlib import get_subprocess_traceparent_headers; "
                "sentry_sdk.init(); "
                "assert os.environ['FOO'] == 'bar'; "
                "print(dict(get_subprocess_traceparent_headers()))",
            ]

            if iterator:
                args = iter(args)

            if positional_args:
                a = (
                    args,
                    0,  # bufsize
                    None,  # executable
                    None,  # stdin
                    subprocess.PIPE,  # stdout
                    None,  # stderr
                    None,  # preexec_fn
                    False,  # close_fds
                    False,  # shell
                    os.getcwd() if with_cwd else None,  # cwd
                )

                if env_mapping is not None:
                    a += (env_mapping,)

                popen = subprocess.Popen(*a)

            else:
                kw = {"args": args, "stdout": subprocess.PIPE}

                if with_cwd:
                    kw["cwd"] = os.getcwd()

                if env_mapping is not None:
                    kw["env"] = env_mapping

                popen = subprocess.Popen(**kw)

            output, unused_err = popen.communicate()
            retcode = popen.poll()
            assert not retcode

        assert os.environ == old_environ

        assert span.trace_id in str(output)

        capture_message("hi")

        (message_event,) = (item.payload for item in items if item.type == "event")
        assert message_event["message"] == "hi"

        data = ApproxDict({})

        sentry_sdk.flush()
        (
            subprocess_init_span,
            subprocess_wait_span,
            subprocess_communicate_span,
            parent_span,
        ) = (item.payload for item in items if item.type == "span")

        assert (
            subprocess_init_span["attributes"]["sentry.op"],
            subprocess_wait_span["attributes"]["sentry.op"],
            subprocess_communicate_span["attributes"]["sentry.op"],
        ) == ("subprocess", "subprocess.wait", "subprocess.communicate")

        # span hierarchy
        assert (
            subprocess_wait_span["parent_span_id"]
            == subprocess_communicate_span["span_id"]
        )

        assert (
            subprocess_communicate_span["parent_span_id"]
            == subprocess_init_span["parent_span_id"]
            == parent_span["span_id"]
        )

        assert (
            subprocess_init_span["attributes"][SPANDATA.PROCESS_PID]
            == subprocess_wait_span["attributes"][SPANDATA.PROCESS_PID]
            == subprocess_communicate_span["attributes"][SPANDATA.PROCESS_PID]
        )

        # data of init span
        assert subprocess_init_span.get("attributes", {}) == data
        if iterator:
            assert "iterator" in subprocess_init_span["name"]
            assert subprocess_init_span["name"].startswith("<")
        else:
            assert sys.executable + " -c" in subprocess_init_span["name"]

    else:
        events = capture_events()

        with start_transaction(name="foo") as transaction:
            args = [
                sys.executable,
                "-c",
                "import os; "
                "import sentry_sdk; "
                "from sentry_sdk.integrations.stdlib import get_subprocess_traceparent_headers; "
                "sentry_sdk.init(); "
                "assert os.environ['FOO'] == 'bar'; "
                "print(dict(get_subprocess_traceparent_headers()))",
            ]

            if iterator:
                args = iter(args)

            if positional_args:
                a = (
                    args,
                    0,  # bufsize
                    None,  # executable
                    None,  # stdin
                    subprocess.PIPE,  # stdout
                    None,  # stderr
                    None,  # preexec_fn
                    False,  # close_fds
                    False,  # shell
                    os.getcwd() if with_cwd else None,  # cwd
                )

                if env_mapping is not None:
                    a += (env_mapping,)

                popen = subprocess.Popen(*a)

            else:
                kw = {"args": args, "stdout": subprocess.PIPE}

                if with_cwd:
                    kw["cwd"] = os.getcwd()

                if env_mapping is not None:
                    kw["env"] = env_mapping

                popen = subprocess.Popen(**kw)

            output, unused_err = popen.communicate()
            retcode = popen.poll()
            assert not retcode

        assert os.environ == old_environ

        assert transaction.trace_id in str(output)

        capture_message("hi")

        (
            transaction_event,
            message_event,
        ) = events

        assert message_event["message"] == "hi"

        data = ApproxDict({"subprocess.cwd": os.getcwd()} if with_cwd else {})

        (crumb,) = message_event["breadcrumbs"]["values"]
        assert crumb == {
            "category": "subprocess",
            "data": data,
            "message": crumb["message"],
            "timestamp": crumb["timestamp"],
            "type": "subprocess",
        }

        if not iterator:
            assert crumb["message"].startswith(sys.executable + " ")

        assert transaction_event["type"] == "transaction"

        (
            subprocess_init_span,
            subprocess_communicate_span,
            subprocess_wait_span,
        ) = transaction_event["spans"]

        assert (
            subprocess_init_span["op"],
            subprocess_communicate_span["op"],
            subprocess_wait_span["op"],
        ) == ("subprocess", "subprocess.communicate", "subprocess.wait")

        assert (
            subprocess_wait_span["parent_span_id"]
            == subprocess_communicate_span["span_id"]
        )

        assert (
            subprocess_communicate_span["parent_span_id"]
            == subprocess_init_span["parent_span_id"]
            == transaction_event["contexts"]["trace"]["span_id"]
        )

        assert (
            subprocess_init_span["tags"]["subprocess.pid"]
            == subprocess_wait_span["tags"]["subprocess.pid"]
            == subprocess_communicate_span["tags"]["subprocess.pid"]
        )

        # data of init span
        assert subprocess_init_span.get("data", {}) == data
        if iterator:
            assert "iterator" in subprocess_init_span["description"]
            assert subprocess_init_span["description"].startswith("<")
        else:
            assert sys.executable + " -c" in subprocess_init_span["description"]


@pytest.mark.parametrize("span_streaming", [True, False])
def test_subprocess_empty_env(
    sentry_init,
    monkeypatch,
    span_streaming,
):
    monkeypatch.setenv("TEST_MARKER", "should_not_be_seen")
    sentry_init(
        integrations=[StdlibIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        with sentry_sdk.traces.start_span(name="custom parent"):
            args = [
                sys.executable,
                "-c",
                "import os; print(os.environ.get('TEST_MARKER', None))",
            ]
            output = subprocess.check_output(args, env={}, universal_newlines=True)
    else:
        with start_transaction(name="foo"):
            args = [
                sys.executable,
                "-c",
                "import os; print(os.environ.get('TEST_MARKER', None))",
            ]
            output = subprocess.check_output(args, env={}, universal_newlines=True)

    assert "should_not_be_seen" not in output


@pytest.mark.parametrize("span_streaming", [True, False])
def test_subprocess_invalid_args(
    sentry_init,
    span_streaming,
):
    sentry_init(
        integrations=[StdlibIntegration()],
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    with pytest.raises(TypeError) as excinfo:
        subprocess.Popen(1)

    assert "'int' object is not iterable" in str(excinfo.value)


@pytest.mark.parametrize("span_streaming", [True, False])
def test_subprocess_span_origin(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        integrations=[StdlibIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            args = [
                sys.executable,
                "-c",
                "print('hello world')",
            ]
            kw = {"args": args, "stdout": subprocess.PIPE}

            popen = subprocess.Popen(**kw)
            popen.communicate()
            popen.poll()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]

        assert spans[3]["attributes"]["sentry.origin"] == "manual"

        assert spans[0]["attributes"]["sentry.op"] == "subprocess"
        assert (
            spans[0]["attributes"]["sentry.origin"]
            == "auto.subprocess.stdlib.subprocess"
        )

        assert spans[1]["attributes"]["sentry.op"] == "subprocess.wait"
        assert (
            spans[1]["attributes"]["sentry.origin"]
            == "auto.subprocess.stdlib.subprocess"
        )

        assert spans[2]["attributes"]["sentry.op"] == "subprocess.communicate"
        assert (
            spans[2]["attributes"]["sentry.origin"]
            == "auto.subprocess.stdlib.subprocess"
        )
    else:
        events = capture_events()

        with start_transaction(name="foo"):
            args = [
                sys.executable,
                "-c",
                "print('hello world')",
            ]
            kw = {"args": args, "stdout": subprocess.PIPE}

            popen = subprocess.Popen(**kw)
            popen.communicate()
            popen.poll()

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"

        assert event["spans"][0]["op"] == "subprocess"
        assert event["spans"][0]["origin"] == "auto.subprocess.stdlib.subprocess"

        assert event["spans"][1]["op"] == "subprocess.communicate"
        assert event["spans"][1]["origin"] == "auto.subprocess.stdlib.subprocess"

        assert event["spans"][2]["op"] == "subprocess.wait"
        assert event["spans"][2]["origin"] == "auto.subprocess.stdlib.subprocess"
