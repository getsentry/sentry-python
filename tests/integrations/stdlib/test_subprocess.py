import os
import platform
import subprocess
import sys

import pytest

from sentry_sdk import capture_message, start_transaction
from sentry_sdk._compat import PY2
from sentry_sdk.integrations.stdlib import StdlibIntegration
from tests.conftest import ApproxDict


if PY2:
    from collections import Mapping
else:
    from collections.abc import Mapping


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
def test_subprocess_basic(
    sentry_init,
    capture_events,
    monkeypatch,
    positional_args,
    iterator,
    env_mapping,
    with_cwd,
):
    monkeypatch.setenv("FOO", "bar")

    old_environ = dict(os.environ)

    sentry_init(integrations=[StdlibIntegration()], traces_sample_rate=1.0)
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

    # span hierarchy
    assert (
        subprocess_wait_span["parent_span_id"] == subprocess_communicate_span["span_id"]
    )
    assert (
        subprocess_communicate_span["parent_span_id"]
        == subprocess_init_span["parent_span_id"]
        == transaction_event["contexts"]["trace"]["span_id"]
    )

    # common data
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


def test_subprocess_invalid_args(sentry_init):
    sentry_init(integrations=[StdlibIntegration()])

    with pytest.raises(TypeError) as excinfo:
        subprocess.Popen(1)

    assert "'int' object is not iterable" in str(excinfo.value)
