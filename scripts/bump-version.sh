#!/bin/bash
set -eux

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd $SCRIPT_DIR/..

OLD_VERSION="${1}"
NEW_VERSION="${2}"

echo "Current version: $OLD_VERSION"
echo "Bumping version: $NEW_VERSION"

sed -i '' -e "1,/^\s*version=/ s/version=\"[^\"]*\"/version=\"$NEW_VERSION\"/" setup.py
