import atexit
import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from sentry_sdk.utils import capture_internal_exceptions, env_to_bool

if TYPE_CHECKING:
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
        "it's",
        "it",
    ),
}


def _init_call_site() -> "Optional[str]":
    """The file that called sentry_sdk.init()."""
    sdk_dir = os.path.dirname(os.path.abspath(__file__)) + os.sep
    frame = sys._getframe()
    while frame is not None:
        # A script's own path is relative on Python 3.8 and older.
        filename = os.path.abspath(frame.f_code.co_filename)
        if not filename.startswith(sdk_dir):
            return filename if os.path.isfile(filename) else None
        frame = frame.f_back
    return None


def _git(cwd: str, *args: str) -> "Optional[str]":
    """A git command's output, or None when git has no answer."""
    startupinfo = None
    if sys.platform == "win32" or sys.platform == "cygwin":
        # Don't let a command prompt window pop up.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=2,
            startupinfo=startupinfo,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


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

    cwd = os.path.dirname(init_file)
    status = _git(cwd, "status", "--porcelain", "--", init_file)
    if status is None:
        return changed >= now - SETUP_IN_PROGRESS_SECONDS_WITHOUT_GIT
    if status:
        return True

    # A checkout also gives a clean file a new mtime, so only count a commit
    # that was made here.
    committer = _git(
        cwd, "log", "-1", f"--since={int(cutoff)}", "--format=%ce", "--", init_file
    )
    return bool(committer) and committer == _git(cwd, "config", "user.email")


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
            # Codex treats instructions in tool output as untrusted and won't turn on
            # more data collection without the user, but it passes a fact on to them.
            hint = (
                f"{off} If you leave {it} off, tell the user {its} off and that you can "
                f"turn {it} on if they want. Sentry's official setup checklist, included "
                f"in the sentry-sdk package you installed: {SETUP_CHECKLIST_PATH}"
            )
        else:
            # Say what Sentry won't show, not which settings are off. An agent that
            # sees the setting names doesn't open the checklist.
            hint = (
                f"{not_shown} The checklist shows what to change. If you're setting up "
                "Sentry, it is strongly recommended to check your setup against Sentry's "
                "official setup checklist, included in the sentry-sdk package you "
                f"installed: {SETUP_CHECKLIST_PATH}"
            )
        sys.stdout.flush()
        sys.stderr.write(f"[sentry] {hint} (hide with SENTRY_AGENT_HINTS=0)\n")
        sys.stderr.flush()


def show_coding_agent_hint(options: "Dict[str, Any]") -> None:
    """Point a coding agent at the setup checklist while Sentry is being set up."""
    global _coding_agent_hint_shown

    rate = options["traces_sample_rate"]
    tracing_on = options["traces_sampler"] is not None or rate not in (0, None)
    logs_on = bool(options["enable_logs"])
    if (
        _coding_agent_hint_shown
        or not options["dsn"]
        or options["agent_setup_complete"]
        or (tracing_on and logs_on)
        or not _running_under_coding_agent()
    ):
        return

    init_file = _init_call_site()
    if init_file is None:
        return

    _coding_agent_hint_shown = True
    # Agents often cut a command's output with `| tail`, so the hint goes last.
    atexit.register(
        _write_coding_agent_hint, init_file, *_MISSING_PRODUCTS[(tracing_on, logs_on)]
    )
