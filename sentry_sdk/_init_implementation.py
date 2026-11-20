import atexit
import inspect
import os
import sys
import time
import warnings
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.utils import (
    _git,
    _is_external_source,
    capture_internal_exceptions,
    env_to_bool,
)

if TYPE_CHECKING:
    from types import FrameType
    from typing import Any, ContextManager, Dict, Optional

    import sentry_sdk.consts


class _InitGuard:
    _CONTEXT_MANAGER_DEPRECATION_WARNING_MESSAGE = (
        "Using the return value of sentry_sdk.init as a context manager "
        "and manually calling the __enter__ and __exit__ methods on the "
        "return value are deprecated. We are no longer maintaining this "
        "functionality, and we will remove it in the next major release."
    )

    def __init__(self, client: "sentry_sdk.Client") -> None:
        self._client = client

    def __enter__(self) -> "_InitGuard":
        warnings.warn(
            self._CONTEXT_MANAGER_DEPRECATION_WARNING_MESSAGE,
            stacklevel=2,
            category=DeprecationWarning,
        )

        return self

    def __exit__(self, exc_type: "Any", exc_value: "Any", tb: "Any") -> None:
        warnings.warn(
            self._CONTEXT_MANAGER_DEPRECATION_WARNING_MESSAGE,
            stacklevel=2,
            category=DeprecationWarning,
        )

        c = self._client
        if c is not None:
            c.close()


def _check_python_deprecations() -> None:
    # Since we're likely to deprecate Python versions in the future, I'm keeping
    # this handy function around. Use this to detect the Python version used and
    # to output logger.warning()s if it's deprecated.
    pass


_coding_agent_hint_shown = False


def _running_under_coding_agent() -> bool:
    if os.environ.get("CI"):
        return False
    if env_to_bool(os.environ.get("SENTRY_AGENT_HINTS"), strict=True) is False:
        return False

    ai_agent = os.environ.get("AI_AGENT")
    if ai_agent:
        return env_to_bool(ai_agent, strict=True) is not False

    # AI_AGENT is the cross-tool convention. These cover agents that don't set it.
    return any(
        os.environ.get(name)
        for name in (
            "CLAUDECODE",
            "CODEX_THREAD_ID",
            "CODEX_SANDBOX",
            "CURSOR_AGENT",
            "GEMINI_CLI",
        )
    )


def _changed_by_this_user(init_file: str) -> bool:
    """Whether the recent change to the file that calls init() was made here.

    A checkout or pull also gives the file a new mtime.
    """

    def git(*args: str) -> "Optional[str]":
        # Read only: don't take the index lock (an agent may be running git at the
        # same time) and don't run a configured fsmonitor command.
        return _git(
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            *args,
            cwd=os.path.dirname(init_file),
            timeout=2,
        )

    status = git("status", "--porcelain", "--", init_file)
    if status is None:
        cutoff = time.time() - 10 * 60
        try:
            return os.stat(init_file).st_mtime >= cutoff
        except OSError:
            return False
    if status:
        return True

    # The file is clean, so only count a commit that was made here.
    since = int(time.time() - 60 * 60)
    committer = git("log", "-1", f"--since={since}", "--format=%ce", "--", init_file)
    return bool(committer) and committer == git("config", "user.email")


def _write_coding_agent_hint(init_file: str, off: str) -> None:
    with capture_internal_exceptions():
        if not _changed_by_this_user(init_file):
            return

        checklist = os.path.join(os.path.dirname(__file__), "SETUP_CHECKLIST.md")
        sys.stdout.flush()
        sys.stderr.write(
            f"[sentry] {off} If you're setting up Sentry, the optional settings are "
            f"listed in {checklist}. Check with the user before enabling them. "
            "(hide with SENTRY_AGENT_HINTS=0)\n"
        )
        sys.stderr.flush()


def show_coding_agent_hint(options: "Dict[str, Any]", caller: "FrameType") -> None:
    """Tell a coding agent which Sentry features are off while Sentry is being set up."""
    global _coding_agent_hint_shown

    rate = options["traces_sample_rate"]
    tracing_on = options["traces_sampler"] is not None or rate not in (0, None)
    logs_on = bool(options["enable_logs"])
    if (
        _coding_agent_hint_shown
        or sys.platform not in ("darwin", "linux")
        or not options["dsn"]
        or (tracing_on and logs_on)
        or not _running_under_coding_agent()
    ):
        return

    # A script's own path is relative on Python 3.8 and older.
    init_file = os.path.abspath(caller.f_code.co_filename)
    # An installed tool that uses Sentry itself isn't the user's app.
    if _is_external_source(init_file):
        return
    try:
        if os.stat(init_file).st_mtime < time.time() - 60 * 60:
            return
    except OSError:
        return

    if not tracing_on and not logs_on:
        off = "Tracing and logs are off, so Sentry only gets errors."
    elif not tracing_on:
        off = "Tracing is off, so Sentry gets errors and logs but no traces."
    else:
        off = "Logs are off, so Sentry gets errors and traces but no logs."

    _coding_agent_hint_shown = True
    # Agents often cut a command's output with `| tail`, so the hint goes last.
    # Git runs at exit, not here, so it doesn't slow down startup.
    atexit.register(_write_coding_agent_hint, init_file, off)


def _init(*args: "Optional[str]", **kwargs: "Any") -> "ContextManager[Any]":
    """Initializes the SDK and optionally integrations.

    This takes the same arguments as the client constructor.
    """
    client = sentry_sdk.Client(*args, **kwargs)
    sentry_sdk.get_global_scope().set_client(client)
    with capture_internal_exceptions():
        # The frame that called sentry_sdk.init(). None on Pythons without frames.
        frame = inspect.currentframe()
        if frame is not None and frame.f_back is not None:
            show_coding_agent_hint(client.options, frame.f_back)
    _check_python_deprecations()
    rv = _InitGuard(client)
    return rv


if TYPE_CHECKING:
    # Make mypy, PyCharm and other static analyzers think `init` is a type to
    # have nicer autocompletion for params.
    #
    # Use `ClientConstructor` to define the argument types of `init` and
    # `ContextManager[Any]` to tell static analyzers about the return type.

    class init(sentry_sdk.consts.ClientConstructor, _InitGuard):
        pass

else:
    # Alias `init` for actual usage. Go through the lambda indirection to throw
    # PyCharm off of the weakly typed signature (it would otherwise discover
    # both the weakly typed signature of `_init` and our faked `init` type).

    init = (lambda: _init)()
