#!/bin/bash

# Usage: sh scripts/runtox.sh py3.12 <pytest-args>
# Runs all environments with substring py3.12 and the given arguments for pytest

set -ex

if [ -n "$TOXPATH" ]; then
    true
elif which tox &> /dev/null; then
    TOXPATH=tox
else
    TOXPATH=./.venv/bin/tox
fi

searchstring="$1"

ENV="$($TOXPATH -l | grep -- "$searchstring" | tr $'\n' ',')"

if [ -z "${ENV}" ]; then
    echo "No targets found. Skipping."
    exit 0
fi

exec $TOXPATH -p auto -o -e "$ENV" -- "${@:2}"
