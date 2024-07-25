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

excludelatest=false
for arg in "$@"
do
    if [ "$arg" = "--exclude-latest" ]; then
        excludelatest=true
        shift
        break
    fi
done

searchstring="$1"

if $excludelatest; then
    echo "Excluding latest"
    ENV="$($TOXPATH -l | grep -- "$searchstring" | grep -v -- '-latest' | tr $'\n' ',')"
else
    echo "Including latest"
    ENV="$($TOXPATH -l | grep -- "$searchstring" | tr $'\n' ',')"
fi

if [ -z "${ENV}" ]; then
    echo "No targets found. Skipping."
    exit 0
fi

exec $TOXPATH -p auto -o -e "$ENV" -- "${@:2}"
