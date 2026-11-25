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
    from typing import List, Tuple

    from sentry_sdk.client import BaseClient

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

GIT_TIMEOUT_SECONDS = 2

_exit_hook_registered = False
# What _write_coding_agent_hint can print at exit: one entry for each file that called
# init() with tracing or logs off.
_hints_at_exit: "List[Tuple[str, bool, bool]]" = []


def _running_under_coding_agent() -> bool:
    if env_to_bool(os.environ.get("CI")):
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
            timeout=GIT_TIMEOUT_SECONDS,
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
            timeout=GIT_TIMEOUT_SECONDS,
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
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return committer == user_email


def _logs_on(client: "BaseClient") -> bool:
    integrations = client.integrations
    return (
        getattr(integrations.get("logging"), "_logs_handler", None) is not None
        or getattr(integrations.get("loguru"), "level", None) is not None
    )


def register_coding_agent_hint_exit_hook() -> None:
    """Call before the client is made. Exit hooks run last-in first-out, so the hint then
    prints after the client's own exit output."""
    global _exit_hook_registered

    if not _exit_hook_registered and _running_under_coding_agent():
        _exit_hook_registered = True
        atexit.register(_write_coding_agent_hint)


def _write_coding_agent_hint() -> None:
    with capture_internal_exceptions():
        # One hint, for the first init() from a file that this user changed.
        for init_file, tracing_on, logs_on in _hints_at_exit:
            if _setup_in_progress(init_file):
                break
        else:
            return

        codex = "codex" in (os.environ.get("AI_AGENT") or "").lower() or any(
            os.environ.get(name) for name in ("CODEX_THREAD_ID", "CODEX_SANDBOX")
        )
        if codex and tracing_on:
            hint = (
                "Logs are off, so Sentry gets errors and traces but no logs. If you "
                "leave them off, tell the user they're off and that you can turn them "
                "on if they want. Sentry's official setup checklist, included in the "
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
                "Sentry will get errors from this app but none of its logs. The "
                "checklist shows what to change. If you're setting "
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
                "around them, and none of its logs. The checklist shows "
                "what to change. If you're setting up Sentry, it is strongly "
                "recommended to check your setup against Sentry's official setup "
                "checklist, included in the sentry-sdk package you installed: "
                f"{SETUP_CHECKLIST_PATH}"
            )
        sys.stdout.flush()
        sys.stderr.write(f"[sentry] {hint} (hide with SENTRY_AGENT_HINTS=0)\n")
        sys.stderr.flush()


def show_coding_agent_hint(client: "BaseClient", caller: "FrameType") -> None:
    options = client.options
    rate = options["traces_sample_rate"]
    tracing_on = options["traces_sampler"] is not None or rate not in (0, None)
    logs_on = _logs_on(client)
    if (
        not _exit_hook_registered
        or sys.version_info < (3, 9)
        or sys.platform not in ("darwin", "linux")
        or not options["dsn"]
        or options["_experiments"].get("agent_setup_complete")
        or (tracing_on and logs_on)
    ):
        return

    init_file = caller.f_code.co_filename
    if _is_external_source(init_file):
        return
    # Agents often cut a command's output with `| tail`, so the hint goes last.
    if all(init_file != seen[0] for seen in _hints_at_exit):
        _hints_at_exit.append((init_file, tracing_on, logs_on))
