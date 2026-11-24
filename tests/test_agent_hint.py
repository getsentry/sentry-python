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
    """Run a script that calls sentry_sdk.init() and return what the SDK prints at exit."""
    for name in ("CI", "SENTRY_AGENT_HINTS", "AI_AGENT", *CODING_AGENT_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sentry_sdk._agent_hint, "_hint_at_exit", None)
    monkeypatch.setattr(sentry_sdk._agent_hint, "_exit_hook_registered", False)
    at_exit = []
    monkeypatch.setattr(sentry_sdk._agent_hint.atexit, "register", at_exit.append)
    app = tmp_path / "app.py"

    def run(
        env, init_args="", before_run=None, inits=1, dsn=AGENT_HINT_DSN, filename=None
    ):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        init = f"sentry_sdk.init(dsn={dsn!r}, {init_args})\n"
        app.write_text("import sentry_sdk\n" + init * inits)
        if before_run:
            before_run(app)

        try:
            exec(compile(app.read_text(), filename or str(app), "exec"), {})
            for hook in at_exit:
                hook()
        finally:
            sentry_sdk.get_client().close()
        return capsys.readouterr().err

    return run


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


@pytest.mark.parametrize(
    "env, init_args",
    [
        ({}, "traces_sample_rate=0"),
        ({"AI_AGENT": "claude-code"}, "traces_sample_rate=0.1, enable_logs=True"),
        (
            {"AI_AGENT": "claude-code"},
            "traces_sample_rate=0.1, _experiments={'enable_logs': True}",
        ),
        ({"AI_AGENT": "claude-code"}, "traces_sampler=lambda _: 1.0, enable_logs=True"),
        ({"AI_AGENT": "claude-code"}, "_experiments={'agent_setup_complete': True}"),
        ({"AI_AGENT": "0"}, "traces_sample_rate=0"),
        ({"AI_AGENT": "claude-code", "CI": "true"}, "traces_sample_rate=0"),
        ({"CLAUDECODE": "1", "SENTRY_AGENT_HINTS": "0"}, "traces_sample_rate=0"),
    ],
)
def test_coding_agent_hint_not_shown(run_app, env, init_args):
    assert "[sentry]" not in run_app(env, init_args)


def test_coding_agent_hint_shown_when_ci_is_set_to_false(run_app):
    env = {"AI_AGENT": "claude-code", "CI": "false"}

    assert SETUP_CHECKLIST_PATH in run_app(env, "traces_sample_rate=0")


def test_coding_agent_hint_not_shown_without_dsn(run_app):
    env = {"AI_AGENT": "claude-code", "SENTRY_DSN": ""}

    assert "[sentry]" not in run_app(env, "traces_sample_rate=0", dsn=None)


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
            "Logs are off, so Sentry gets errors and traces but no logs. If you leave them off, tell the user they're off",
        ),
    ],
)
def test_coding_agent_hint_per_agent(run_app, env, init_args, hint):
    printed = run_app(env, init_args)

    assert f"[sentry] {hint}" in printed
    assert SETUP_CHECKLIST_PATH in printed


def test_coding_agent_hint_not_shown_for_an_installed_tool(run_app, tmp_path):
    tool = tmp_path / "site-packages" / "sometool" / "cli.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("")

    assert run_app({"AI_AGENT": "claude-code"}, filename=str(tool)) == ""


def test_coding_agent_hint_shown_once(run_app):
    printed = run_app({"AI_AGENT": "claude-code"}, "traces_sample_rate=0", inits=2)

    assert printed.count(SETUP_CHECKLIST_PATH) == 1


def _git(app, *args, committer="dev@example.com", date=None):
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Dev",
        "GIT_AUTHOR_EMAIL": committer,
        "GIT_COMMITTER_NAME": "Dev",
        "GIT_COMMITTER_EMAIL": committer,
    }
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", *args],
        cwd=str(app.parent),
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _commit(app, **kwargs):
    _git(app, "init", "-q")
    _git(app, "config", "user.email", "dev@example.com")
    _git(app, "add", app.name)
    _git(app, "commit", "-q", "-m", "Set up Sentry", **kwargs)


def _changed_minutes_ago(app, minutes):
    changed = time.time() - minutes * 60
    os.utime(app, (changed, changed))


def _committed_and_edited(app, minutes_ago=0):
    _commit(app, date="2020-01-01T00:00:00")
    app.write_text(app.read_text() + "\n")
    _changed_minutes_ago(app, minutes_ago)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_coding_agent_hint_with_a_relative_script_path(run_app, tmp_path, monkeypatch):
    # Only git can tell that a change from 20 minutes ago is this user's.
    monkeypatch.chdir(tmp_path)
    printed = run_app(
        {"AI_AGENT": "claude-code"},
        before_run=lambda app: _committed_and_edited(app, minutes_ago=20),
        filename="app.py",
    )

    assert SETUP_CHECKLIST_PATH in printed


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(
    "before_run, shown",
    [
        pytest.param(None, True, id="new file, no git repo"),
        pytest.param(
            lambda app: _changed_minutes_ago(app, 20),
            False,
            id="no git repo, changed 20 minutes ago",
        ),
        pytest.param(_committed_and_edited, True, id="uncommitted change"),
        pytest.param(
            lambda app: _committed_and_edited(app, minutes_ago=20),
            True,
            id="uncommitted change, 20 minutes ago",
        ),
        pytest.param(
            lambda app: _committed_and_edited(app, minutes_ago=120),
            False,
            id="uncommitted change, two hours ago",
        ),
        pytest.param(_commit, True, id="committed here a moment ago"),
        pytest.param(
            lambda app: _commit(app, committer="teammate@example.com"),
            False,
            id="a teammate's new commit",
        ),
        pytest.param(
            lambda app: _commit(app, date="2020-01-01T00:00:00"),
            False,
            id="fresh checkout of an old commit",
        ),
    ],
)
def test_coding_agent_hint_only_during_setup(run_app, before_run, shown):
    printed = run_app({"AI_AGENT": "claude-code"}, before_run=before_run)

    assert (SETUP_CHECKLIST_PATH in printed) is shown


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
