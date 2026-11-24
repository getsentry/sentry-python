import atexit
import os
import sys
import time
from typing import TYPE_CHECKING

from sentry_sdk.utils import (
    _git,
    _is_external_source,
    capture_internal_exceptions,
    env_to_bool,
)

if TYPE_CHECKING:
    from types import FrameType
    from typing import Any, Dict, Optional

SETUP_CHECKLIST_PATH = os.path.join(os.path.dirname(__file__), "SETUP_CHECKLIST.md")

# Set by coding agents in the commands they run. AI_AGENT is the cross-tool
# convention; the rest cover agents that don't set it.
CODING_AGENT_ENV_VARS = (
    "CLAUDECODE",
    "CODEX_THREAD_ID",
    "CODEX_SANDBOX",
    "CURSOR_AGENT",
    "GEMINI_CLI",
)

# How long after a change to the file that calls init() the setup counts as in progress.
# Without git we can't tell whose change it is, so the window is shorter.
SETUP_IN_PROGRESS_SECONDS = 60 * 60
SETUP_IN_PROGRESS_SECONDS_WITHOUT_GIT = 10 * 60

GIT_TIMEOUT_SECONDS = 2

_coding_agent_hint_shown = False


def _running_under_coding_agent() -> bool:
    if os.environ.get("CI"):
        return False
    if env_to_bool(os.environ.get("SENTRY_AGENT_HINTS"), strict=True) is False:
        return False

    ai_agent = os.environ.get("AI_AGENT")
    if ai_agent:
        return env_to_bool(ai_agent, strict=True) is not False

    return any(os.environ.get(name) for name in CODING_AGENT_ENV_VARS)


_MISSING_PRODUCTS = {
    (False, False): (
        "Sentry will get errors from this app but not the request trace around them, "
        "and its logs only as breadcrumbs.",
        "Tracing and logs are off, so Sentry only gets errors.",
        "they're",
        "them",
    ),
    (False, True): (
        "Sentry will get errors from this app but not the request trace around them.",
        "Tracing is off, so Sentry gets errors and logs but no traces.",
        "it's",
        "it",
    ),
    (True, False): (
        "Sentry will get errors from this app but its logs only as breadcrumbs.",
        "Logs are off, so Sentry gets errors and traces but no logs.",
        "they're",
        "them",
    ),
}


def _setup_in_progress(init_file: str) -> bool:
    """Whether this user changed the file that calls init() a short time ago."""
    now = time.time()
    cutoff = now - SETUP_IN_PROGRESS_SECONDS
    try:
        changed = os.stat(init_file).st_mtime
    except OSError:
        return False
    if changed < cutoff:
        return False

    def git(*args: str) -> "Optional[str]":
        # Read only: don't take the index lock (the user or an agent may be running
        # git at the same time) and don't run a configured fsmonitor command.
        return _git(
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            *args,
            cwd=os.path.dirname(init_file),
            timeout=GIT_TIMEOUT_SECONDS,
        )

    # --branch adds a header line, so an empty answer means git has no answer.
    status = git("status", "--porcelain", "--branch", "--", init_file)
    if not status:
        return changed >= now - SETUP_IN_PROGRESS_SECONDS_WITHOUT_GIT
    if "\n" in status:
        return True

    # A checkout also gives a clean file a new mtime, so only count a commit
    # that was made here.
    committer = git(
        "log", "-1", f"--since={int(cutoff)}", "--format=%ce", "--", init_file
    )
    return bool(committer) and committer == git("config", "user.email")


def _write_coding_agent_hint(
    init_file: str, not_shown: str, off: str, its: str, it: str
) -> None:
    with capture_internal_exceptions():
        if not _setup_in_progress(init_file):
            return

        codex = "codex" in (os.environ.get("AI_AGENT") or "").lower() or any(
            os.environ.get(name) for name in ("CODEX_THREAD_ID", "CODEX_SANDBOX")
        )
        if codex:
            hint = (
                f"{off} If you leave {it} off, tell the user {its} off and that you can "
                f"turn {it} on if they want. Sentry's official setup checklist, included "
                f"in the sentry-sdk package you installed: {SETUP_CHECKLIST_PATH}"
            )
        else:
            hint = (
                f"{not_shown} The checklist shows what to change. If you're setting up "
                "Sentry, it is strongly recommended to check your setup against Sentry's "
                "official setup checklist, included in the sentry-sdk package you "
                f"installed: {SETUP_CHECKLIST_PATH}"
            )
        sys.stdout.flush()
        sys.stderr.write(f"[sentry] {hint} (hide with SENTRY_AGENT_HINTS=0)\n")
        sys.stderr.flush()


def show_coding_agent_hint(options: "Dict[str, Any]", caller: "FrameType") -> None:
    """Point a coding agent at the setup checklist while Sentry is being set up."""
    global _coding_agent_hint_shown

    rate = options["traces_sample_rate"]
    tracing_on = options["traces_sampler"] is not None or rate not in (0, None)
    logs_on = bool(options["enable_logs"] or options["_experiments"].get("enable_logs"))
    if (
        _coding_agent_hint_shown
        or sys.platform not in ("darwin", "linux")
        or not options["dsn"]
        or options["_experiments"].get("agent_setup_complete")
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
        if os.stat(init_file).st_mtime < time.time() - SETUP_IN_PROGRESS_SECONDS:
            return
    except OSError:
        return

    _coding_agent_hint_shown = True
    # Agents often cut a command's output with `| tail`, so the hint goes last.
    atexit.register(
        _write_coding_agent_hint, init_file, *_MISSING_PRODUCTS[(tracing_on, logs_on)]
    )
