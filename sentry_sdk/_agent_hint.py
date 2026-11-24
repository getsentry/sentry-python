import atexit
import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from sentry_sdk.utils import (
    _is_external_source,
    capture_internal_exceptions,
    env_to_bool,
)

if TYPE_CHECKING:
    from types import FrameType
    from typing import Any, Dict

SETUP_CHECKLIST_PATH = os.path.join(os.path.dirname(__file__), "SETUP_CHECKLIST.md")

CODING_AGENT_ENV_VARS = (
    "CLAUDECODE",
    "CODEX_THREAD_ID",
    "CODEX_SANDBOX",
    "CURSOR_AGENT",
    "GEMINI_CLI",
)

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


def _setup_in_progress(init_file: str) -> bool:
    now = time.time()
    cutoff = now - SETUP_IN_PROGRESS_SECONDS
    try:
        changed = os.stat(init_file).st_mtime
    except OSError:
        return False
    if changed < cutoff:
        return False

    cwd = os.path.dirname(init_file)

    # --branch adds a header line, so an empty answer means git has no answer.
    try:
        status = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "status",
                "--porcelain",
                "--branch",
                "--",
                init_file,
            ],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            timeout=0.25,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        status = ""
    if not status:
        return changed >= now - SETUP_IN_PROGRESS_SECONDS_WITHOUT_GIT
    if "\n" in status:
        return True

    # A checkout also gives a clean file a new mtime, so only count a commit
    # that was made here.
    try:
        committer = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "log",
                "-1",
                f"--since={int(cutoff)}",
                "--format=%ce",
                "--",
                init_file,
            ],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            timeout=0.25,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    if not committer:
        return False

    try:
        user_email = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "config",
                "user.email",
            ],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            timeout=0.25,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return committer == user_email


def _write_coding_agent_hint(init_file: str, tracing_on: bool, logs_on: bool) -> None:
    with capture_internal_exceptions():
        if not _setup_in_progress(init_file):
            return

        codex = "codex" in (os.environ.get("AI_AGENT") or "").lower() or any(
            os.environ.get(name) for name in ("CODEX_THREAD_ID", "CODEX_SANDBOX")
        )
        if codex and tracing_on:
            hint = (
                "Logs are off, so Sentry gets errors and traces but no logs. If you "
                "leave it off, tell the user it's off and that you can turn it on if "
                "they want. Sentry's official setup checklist, included in the "
                f"sentry-sdk package you installed: {SETUP_CHECKLIST_PATH}"
            )
        elif codex and logs_on:
            hint = (
                "Tracing is off, so Sentry gets errors and logs but no traces. If you "
                "leave it off, tell the user it's off and that you can turn it on if "
                "they want. Sentry's official setup checklist, included in the "
                f"sentry-sdk package you installed: {SETUP_CHECKLIST_PATH}"
            )
        elif codex:
            hint = (
                "Tracing and logs are off, so Sentry only gets errors. If you leave "
                "them off, tell the user they're off and that you can turn them on if "
                "they want. Sentry's official setup checklist, included in the "
                f"sentry-sdk package you installed: {SETUP_CHECKLIST_PATH}"
            )
        elif tracing_on:
            hint = (
                "Sentry will get errors from this app but its logs only as "
                "breadcrumbs. The checklist shows what to change. If you're setting "
                "up Sentry, it is strongly recommended to check your setup against "
                "Sentry's official setup checklist, included in the sentry-sdk "
                f"package you installed: {SETUP_CHECKLIST_PATH}"
            )
        elif logs_on:
            hint = (
                "Sentry will get errors from this app but not the request trace "
                "around them. The checklist shows what to change. If you're setting "
                "up Sentry, it is strongly recommended to check your setup against "
                "Sentry's official setup checklist, included in the sentry-sdk "
                f"package you installed: {SETUP_CHECKLIST_PATH}"
            )
        else:
            hint = (
                "Sentry will get errors from this app but not the request trace "
                "around them, and its logs only as breadcrumbs. The checklist shows "
                "what to change. If you're setting up Sentry, it is strongly "
                "recommended to check your setup against Sentry's official setup "
                "checklist, included in the sentry-sdk package you installed: "
                f"{SETUP_CHECKLIST_PATH}"
            )
        sys.stdout.flush()
        sys.stderr.write(f"[sentry] {hint} (hide with SENTRY_AGENT_HINTS=0)\n")
        sys.stderr.flush()


def show_coding_agent_hint(options: "Dict[str, Any]", caller: "FrameType") -> None:
    global _coding_agent_hint_shown

    rate = options["traces_sample_rate"]
    tracing_on = options["traces_sampler"] is not None or rate not in (0, None)
    logs_on = bool(options["enable_logs"])
    if (
        _coding_agent_hint_shown
        or sys.version_info < (3, 9)
        or sys.platform not in ("darwin", "linux")
        or not options["dsn"]
        or options["agent_setup_complete"]
        or (tracing_on and logs_on)
        or not _running_under_coding_agent()
    ):
        return

    init_file = caller.f_code.co_filename
    if _is_external_source(init_file):
        return
    try:
        if os.stat(init_file).st_mtime < time.time() - SETUP_IN_PROGRESS_SECONDS:
            return
    except OSError:
        return

    _coding_agent_hint_shown = True
    # Agents often cut a command's output with `| tail`, so the hint goes last.
    atexit.register(_write_coding_agent_hint, init_file, tracing_on, logs_on)
