#!/bin/bash
set -ex

if [ -n "$TOXPATH" ]; then
    true
elif which tox &> /dev/null; then
    TOXPATH=tox
else
    TOXPATH=./.venv/bin/tox
fi

# Usage: sh scripts/runtox.sh py3.7 <pytest-args>
# Runs all environments with substring py3.7 and the given arguments for pytest

if [ -n "$1" ]; then
    searchstring="$1"
elif [ -n "$TRAVIS_PYTHON_VERSION" ]; then
    searchstring="$(echo py$TRAVIS_PYTHON_VERSION | sed -e 's/pypypy/pypy/g' -e 's/-dev//g')"
elif [ -n "$AZURE_PYTHON_VERSION" ]; then
    searchstring="$(echo py$AZURE_PYTHON_VERSION | sed -e 's/pypypy/pypy/g' -e 's/-dev//g')"
fi

exec $TOXPATH -e $($TOXPATH -l | grep "$searchstring" | tr '\n' ',') -- "${@:2}"
