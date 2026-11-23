import os
import shutil
import subprocess
import sys
import time

import pytest

import sentry_sdk
import sentry_sdk._agent_hint
from sentry_sdk._agent_hint import CODING_AGENT_ENV_VARS, SETUP_CHECKLIST_PATH

AGENT_HINT_DSN = "https://public@sentry.example.com/1"


@pytest.fixture
def run_app(tmp_path, monkeypatch, capsys):
    for name in ("CI", "SENTRY_AGENT_HINTS", "AI_AGENT", *CODING_AGENT_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sentry_sdk._agent_hint, "_coding_agent_hint_shown", False)
    at_exit = []
    monkeypatch.setattr(
        sentry_sdk._agent_hint.atexit,
        "register",
        lambda hook, *args: at_exit.append((hook, args)),
    )
    app = tmp_path / "app.py"

    def run(env, init_args="", before_run=None, inits=1):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        init = f"sentry_sdk.init(dsn={AGENT_HINT_DSN!r}, {init_args})\n"
        app.write_text("import sentry_sdk\n" + init * inits)
        if before_run:
            before_run(app)

        try:
            exec(compile(app.read_text(), str(app), "exec"), {})
            for hook, args in at_exit:
                if hook is sentry_sdk._agent_hint._write_coding_agent_hint:
                    hook(*args)
        finally:
            sentry_sdk.get_client().close()
        return capsys.readouterr().err

    return run


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="Python < 3.9 hits an early return"
)
@pytest.mark.parametrize(
    "env, init_args",
    [
        ({"AI_AGENT": "claude-code"}, "traces_sample_rate=0"),
        ({"CLAUDECODE": "1"}, ""),
        ({"CODEX_THREAD_ID": "abc"}, "traces_sample_rate=0.0"),
        ({"CODEX_SANDBOX": "seatbelt"}, "traces_sample_rate=0.1"),
        ({"CLAUDECODE": "1"}, "enable_logs=True"),
    ],
)
def test_coding_agent_hint(run_app, env, init_args):
    assert SETUP_CHECKLIST_PATH in run_app(env, init_args)


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="Python < 3.9 hits an early return"
)
@pytest.mark.parametrize(
    "env, init_args, hint",
    [
        (
            {"CLAUDECODE": "1"},
            "",
            "Sentry will get errors from this app but not the request trace around them, and its logs only as breadcrumbs. The checklist shows what to change. If you're setting up Sentry, it is strongly recommended",
        ),
        (
            {"CLAUDECODE": "1"},
            "enable_logs=True",
            "Sentry will get errors from this app but not the request trace around them. The checklist shows what to change.",
        ),
        (
            {"CLAUDECODE": "1"},
            "traces_sample_rate=0.1",
            "Sentry will get errors from this app but its logs only as breadcrumbs. The checklist shows what to change.",
        ),
        (
            {"CODEX_THREAD_ID": "1"},
            "",
            "Tracing and logs are off, so Sentry only gets errors. If you leave them off, tell the user they're off and that you can turn them on if they want.",
        ),
        (
            {"AI_AGENT": "codex"},
            "enable_logs=True",
            "Tracing is off, so Sentry gets errors and logs but no traces. If you leave it off, tell the user it's off and that you can turn it on if they want.",
        ),
        (
            {"AI_AGENT": "codex"},
            "traces_sample_rate=0.1",
            "Logs are off, so Sentry gets errors and traces but no logs. If you leave it off, tell the user it's off",
        ),
    ],
)
def test_coding_agent_hint_per_agent(run_app, env, init_args, hint):
    printed = run_app(env, init_args)

    assert f"[sentry] {hint}" in printed
    assert SETUP_CHECKLIST_PATH in printed


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="Python < 3.9 hits an early return"
)
def test_coding_agent_hint_shown_once(run_app):
    printed = run_app({"AI_AGENT": "claude-code"}, "traces_sample_rate=0", inits=2)

    assert printed.count(SETUP_CHECKLIST_PATH) == 1


def _commit(app, date=None):
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Dev",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "Dev",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
    }
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    for args in (
        ["init", "-q"],
        ["config", "user.email", "dev@example.com"],
        ["add", app.name],
        ["commit", "-q", "-m", "Set up Sentry"],
    ):
        subprocess.run(
            ["git", *args],
            cwd=str(app.parent),
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


def _committed_and_edited(app, minutes_ago=0):
    _commit(app, date="2020-01-01T00:00:00")
    app.write_text(app.read_text() + "\n")
    changed = time.time() - minutes_ago * 60
    os.utime(app, (changed, changed))


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="Python < 3.9 hits an early return"
)
@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(
    "before_run",
    [
        pytest.param(None, id="new file, no git repo"),
        pytest.param(_committed_and_edited, id="uncommitted change"),
        pytest.param(
            lambda app: _committed_and_edited(app, minutes_ago=20),
            id="uncommitted change, 20 minutes ago",
        ),
        pytest.param(_commit, id="committed here a moment ago"),
    ],
)
def test_coding_agent_hint_during_setup(run_app, before_run):
    printed = run_app({"AI_AGENT": "claude-code"}, before_run=before_run)

    assert SETUP_CHECKLIST_PATH in printed


def test_setup_checklist_ships_with_the_package():
    with open(SETUP_CHECKLIST_PATH) as f:
        assert "agent_setup_complete=True" in f.read()
