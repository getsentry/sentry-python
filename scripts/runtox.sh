#!/bin/sh
if [ -z "$1" ]; then
    searchstring="$(echo py$TRAVIS_PYTHON_VERSION | sed -e 's/pypypy/pypy/g' -e 's/-dev//g')"
else
    searchstring="$1"
fi

exec tox -e $(tox -l | grep $searchstring | tr '\n' ',')
