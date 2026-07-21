#!/bin/bash

# Usage: sh scripts/runtox.sh py3.12 <pytest-args>
# Runs all environments with substring py3.12 and the given arguments for pytest

set -ex

searchstring="$1"

# Filter out -latest environments unless explicitly requested
if [[ "$searchstring" == *-latest* ]]; then
    ENV="$(uv run tox -l | grep -- "$searchstring" | tr $'\n' ',')"
else
    ENV="$(uv run tox -l | grep -- "$searchstring" | grep -v -- '-latest$' | tr $'\n' ',')"
fi

if [ -z "${ENV}" ]; then
    echo "No targets found. Skipping."
    exit 0
fi

# `uv run` itself uses the workspace venv's interpreter (uv's default
# `managed` preference). tox-uv, which provisions per-env interpreters, may
# need a different preference (e.g. `only-system` inside the 3.6/3.7
# container); the workflow passes that via TOX_UV_PYTHON_PREFERENCE.
TOX_ENV=()
if [ -n "$TOX_UV_PYTHON_PREFERENCE" ]; then
    TOX_ENV=(env "UV_PYTHON_PREFERENCE=$TOX_UV_PYTHON_PREFERENCE")
fi

# Django envs used to run serially: with per-fork database creation the
# parallel scheduler deadlocked the shared Postgres service container. Each
# env now builds one reusable database (tox.ini setenv + the django_db_setup
# override in tests/integrations/django/conftest.py), so parallel is safe.
exec uv run "${TOX_ENV[@]}" tox -p auto -o -e "$ENV" -- "${@:2}"
