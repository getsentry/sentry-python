# How to contribute to the Sentry Python SDK

`sentry-sdk` is an ordinary Python package. You can install it with `pip
install -e .` into some virtualenv, edit the sourcecode and test out your
changes manually.

## Running tests and linters

Make sure you have `virtualenv` installed, and the Python versions you care
about. You should have Python 2.7 and the latest Python 3 installed.

You don't need to `workon` or `activate` anything, the `Makefile` will create
one for you. Run `make` or `make help` to list commands.

## Releasing a new version

We use [craft](https://github.com/getsentry/craft#python-package-index-pypi) to
release new versions. You need credentials for the `getsentry` PyPI user, and
must have `twine` installed globally.

The usual release process goes like this:

1. Go through git log and write new entry into `CHANGELOG.md`, commit to master
2. `craft p a.b.c`
3. `craft pp a.b.c`
