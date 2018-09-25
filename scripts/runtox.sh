#!/bin/sh

# Usage: sh scripts/runtox.sh py3.7 <pytest-args>
# Runs all environments with substring py3.7 and the given arguments for pytest
set -xe
if [ -z "$1" ]; then
    searchstring="$(echo py$TRAVIS_PYTHON_VERSION | sed -e 's/pypypy/pypy/g' -e 's/-dev//g')"
else
    searchstring="$1"
fi

coverage erase
exec tox -e $(tox -l | grep $searchstring | tr '\n' ',') -- "${@:2}"
