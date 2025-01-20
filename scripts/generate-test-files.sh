#!/bin/bash

# This script generates tox.ini and CI YAML files in one go.

set -xe

SCRIPT_DIR="$( cd -- "$( dirname -- "$0" )" && pwd )"

python -m venv .venv
source .venv/bin/activate

pip install -r "$SCRIPT_DIR/populate_tox/requirements.txt"
pip install -r "$SCRIPT_DIR/split_tox_gh_actions/requirements.txt"

python "$SCRIPT_DIR/populate_tox/populate_tox.py"
python "$SCRIPT_DIR/scripts/split_tox_gh_actions/split_tox_gh_actions.py"
