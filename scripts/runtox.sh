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

# Django ASGI tests deadlock under tox's parallel env scheduler when multiple
# Django envs share the same Postgres service container. Run those serially.
if [[ "$searchstring" == *-django* ]]; then
    exec uv run tox -e "$ENV" -- "${@:2}"
else
    exec uv run tox -p auto -o -e "$ENV" -- "${@:2}"
fi
