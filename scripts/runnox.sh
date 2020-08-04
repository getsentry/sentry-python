#!/bin/bash
set -ex

if [ -n "$NOXPATH" ]; then
    true
elif which nox &> /dev/null; then
    NOXPATH=nox
else
    NOXPATH=./.venv/bin/nox
fi

# Usage: sh scripts/runtox.sh py3.7 <pytest-args>
# Runs all environments with substring py3.7 and the given arguments for pytest

if [ -n "$1" ]; then
    searchstring="$1"
elif [ -n "$TRAVIS_PYTHON_VERSION" ]; then
    searchstring="$(echo py$TRAVIS_PYTHON_VERSION | sed -e 's/pypypy/pypy/g' -e 's/-dev//g')"
elif [ -n "$AZURE_PYTHON_VERSION" ]; then
    searchstring="$(echo py$AZURE_PYTHON_VERSION | sed -e 's/pypypy/pypy/g' -e 's/-dev//g')"
    if [ "$searchstring" = pypy2 ]; then
        searchstring=pypy
    fi
fi

exec $NOXPATH -k "$searchstring"
