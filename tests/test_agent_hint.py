import os
import shutil
import subprocess
import sys
import time
import types

import pytest

import sentry_sdk
import sentry_sdk._agent_hint
from sentry_sdk._agent_hint import CODING_AGENT_ENV_VARS, SETUP_CHECKLIST_PATH

AGENT_HINT_DSN = "https://public@sentry.example.com/1"


@pytest.fixture
def run_app(tmp_path, monkeypatch, capsys):
    for name in ("CI", "SENTRY_AGENT_HINTS", "AI_AGENT", *CODING_AGENT_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sentry_sdk._agent_hint, "_hints_at_exit", [])
    monkeypatch.setattr(sentry_sdk._agent_hint, "_exit_hook_registered", False)
    at_exit = []
    monkeypatch.setattr(sentry_sdk._agent_hint.atexit, "register", at_exit.append)
    app = tmp_path / "app.py"

    def run(env, init_args="", before_run=None, inits=1):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        init = f"sentry_sdk.init(dsn={AGENT_HINT_DSN!r}, {init_args})\n"
        imports = (
            "import sentry_sdk\n"
            "from sentry_sdk.integrations.logging import LoggingIntegration\n"
        )
        app.write_text(imports + init * inits)
        if before_run:
            before_run(app)

        try:
            exec(compile(app.read_text(), str(app), "exec"), {})
            for hook in at_exit:
                hook()
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
        ({"CLAUDECODE": "1"}, "integrations=[LoggingIntegration()]"),
    ],
)
def test_coding_agent_hint(run_app, env, init_args):
    assert SETUP_CHECKLIST_PATH in run_app(env, init_args)


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="Python < 3.9 hits an early return"
)
def test_coding_agent_hint_shown_when_ci_is_set_to_false(run_app):
    env = {"AI_AGENT": "claude-code", "CI": "false"}

    assert SETUP_CHECKLIST_PATH in run_app(env, "traces_sample_rate=0")


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="Python < 3.9 hits an early return"
)
@pytest.mark.parametrize(
    "env, init_args, hint",
    [
        (
            {"CLAUDECODE": "1"},
            "",
            "Sentry will get errors from this app but not the request trace around them, and none of its logs. The checklist shows what to change. If you're setting up Sentry, it is strongly recommended",
        ),
        (
            {"CLAUDECODE": "1"},
            "integrations=[LoggingIntegration()]",
            "Sentry will get errors from this app but not the request trace around them. The checklist shows what to change.",
        ),
        (
            {"CLAUDECODE": "1"},
            "traces_sample_rate=0.1",
            "Sentry will get errors from this app but none of its logs. The checklist shows what to change.",
        ),
        (
            {"CODEX_THREAD_ID": "1"},
            "",
            "Tracing and logs are off, so Sentry only gets errors. If you leave them off, tell the user they're off and that you can turn them on if they want.",
        ),
        (
            {"AI_AGENT": "codex"},
            "integrations=[LoggingIntegration()]",
            "Tracing is off, so Sentry gets errors and logs but no traces. If you leave it off, tell the user it's off and that you can turn it on if they want.",
        ),
        (
            {"AI_AGENT": "codex"},
            "traces_sample_rate=0.1",
            "Logs are off, so Sentry gets errors and traces but no logs. If you leave them off, tell the user they're off",
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
def test_coding_agent_hint_when_the_logging_integration_sends_no_logs(run_app):
    printed = run_app(
        {"CLAUDECODE": "1"},
        "traces_sample_rate=0.1, integrations=[LoggingIntegration(level=None)]",
    )

    assert "but none of its logs." in printed


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


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="Python < 3.9 hits an early return"
)
def test_coding_agent_hint_when_git_does_not_answer_in_time(run_app, monkeypatch):
    real_communicate = subprocess.Popen.communicate

    def communicate(self, *args, timeout=None, **kwargs):
        if timeout is not None:
            raise subprocess.TimeoutExpired("git", timeout)
        return real_communicate(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", communicate)

    # The file counts as changed without git: it was written a moment ago.
    assert SETUP_CHECKLIST_PATH in run_app({"CLAUDECODE": "1"})


def test_coding_agent_hint_exit_hook_is_registered_before_the_client(
    run_app, monkeypatch
):
    registered_first = []
    real_client = sentry_sdk.Client

    def client(*args, **kwargs):
        registered_first.append(sentry_sdk._agent_hint._exit_hook_registered)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(sentry_sdk, "Client", client)
    run_app({"CLAUDECODE": "1"})

    # The client's own exit hook is registered when the client is made, and exit hooks
    # run last-in first-out, so the hint prints after the client's exit output.
    assert registered_first == [True]


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="Python < 3.9 hits an early return"
)
def test_coding_agent_hint_for_a_later_init_from_a_changed_file(run_app, tmp_path):
    # Changed in the last hour, but not in the last 10 minutes, and there is no git
    # here to say whose change it is.
    old_file = tmp_path / "settings.py"
    old_file.write_text("")
    half_an_hour_ago = time.time() - 30 * 60
    os.utime(old_file, (half_an_hour_ago, half_an_hour_ago))

    def init_from_the_old_file_first(app):
        *imports, init = app.read_text().splitlines(keepends=True)
        first = f"exec(compile({init!r}, {str(old_file)!r}, 'exec'))\n"
        app.write_text("".join(imports) + first + init)

    printed = run_app({"CLAUDECODE": "1"}, before_run=init_from_the_old_file_first)

    assert SETUP_CHECKLIST_PATH in printed


def test_setup_checklist_ships_with_the_package():
    with open(SETUP_CHECKLIST_PATH) as f:
        assert '_experiments={"agent_setup_complete": True}' in f.read()


def test_setup_checklist_example_runs(monkeypatch):
    with open(SETUP_CHECKLIST_PATH) as f:
        example = f.read().split("```python\n")[1].split("```")[0]
    myapp = types.ModuleType("myapp")
    myapp.settings = types.SimpleNamespace(DEBUG=True)
    monkeypatch.setitem(sys.modules, "myapp", myapp)
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    try:
        exec(example, {})
    finally:
        sentry_sdk.get_client().close()
