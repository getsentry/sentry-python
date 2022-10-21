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
elif [ -n "$CI_PYTHON_VERSION" ]; then
    searchstring="$(echo py$CI_PYTHON_VERSION | sed -e 's/pypypy/pypy/g' -e 's/-dev//g')"
    if [ "$searchstring" = "pypy-2.7" ]; then
        searchstring=pypy
    fi
elif [ -n "$AZURE_PYTHON_VERSION" ]; then
    searchstring="$(echo py$AZURE_PYTHON_VERSION | sed -e 's/pypypy/pypy/g' -e 's/-dev//g')"
    if [ "$searchstring" = pypy2 ]; then
        searchstring=pypy
    fi
fi

export TOX_PARALLEL_NO_SPINNER=1
exec $TOXPATH -p auto -e $($TOXPATH -l | grep "$searchstring" | tr $'\n' ',') -- "${@:2}"
