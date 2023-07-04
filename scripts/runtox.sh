#!/bin/bash

# Usage: sh scripts/runtox.sh py3.7 <pytest-args>
# Runs all environments with substring py3.7 and the given arguments for pytest

set -ex

if [ -n "$TOXPATH" ]; then
    true
elif which tox &> /dev/null; then
    TOXPATH=tox
else
    TOXPATH=./.venv/bin/tox
fi

searchstring="$1"

export TOX_PARALLEL_NO_SPINNER=1
ENV="$($TOXPATH -l | grep "$searchstring" | tr $'\n' ',')"

# Run the common 2.7 suite without the -p flag, otherwise we hit an encoding
# issue in tox.
if [ "$ENV" = py2.7-common, ] || [ "$ENV" = py2.7-gevent, ]; then
    exec $TOXPATH -vv -e "$ENV" -- "${@:2}"
else
    exec $TOXPATH -vv -p auto -e "$ENV" -- "${@:2}"
fi
