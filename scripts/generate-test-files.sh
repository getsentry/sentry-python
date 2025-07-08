#!/bin/sh

# This script generates tox.ini and CI YAML files in one go.

set -xe

cd "$(dirname "$0")"

rm -rf toxgen.venv
python -m venv toxgen.venv
. toxgen.venv/bin/activate

pip install -e ..
pip install -r populate_tox/requirements.txt
pip install -r split_tox_gh_actions/requirements.txt

toxgen.venv/bin/python populate_tox/populate_tox.py
toxgen.venv/bin/python split_tox_gh_actions/split_tox_gh_actions.py
