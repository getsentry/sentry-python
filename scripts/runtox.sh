#!/bin/sh
pyversion="$(echo py$TRAVIS_PYTHON_VERSION | sed -e 's/pypypy/pypy/g' -e 's/-dev//g')"
exec tox -e $(tox -l | grep $pyversion | tr '\n' ',')
