#!/bin/sh

# This script generates tox.ini and CI YAML files in one go.

set -xe

cd "$(dirname "$0")"

export UV_PROJECT_ENVIRONMENT=toxgen.venv

uv run --python 3.14t --group toxgen --with-editable .. python populate_tox/populate_tox.py
uv run --python 3.14t --group toxgen --with-editable .. python split_tox_gh_actions/split_tox_gh_actions.py
