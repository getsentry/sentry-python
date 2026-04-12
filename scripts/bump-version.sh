#!/bin/bash
set -eux

if [ "$(uname -s)" != "Linux" ]; then
    echo "Please use the GitHub Action."
    exit 1
fi

SCRIPT_DIR="$( dirname "$0" )"
cd $SCRIPT_DIR/..

echo "Current version: $CRAFT_OLD_VERSION"
echo "Bumping version: $CRAFT_NEW_VERSION"

function replace() {
    ! grep "$2" $3
    perl -i -pe "s/$1/$2/g" $3
    grep "$2" $3  # verify that replacement was successful
}

replace "version=\"$CRAFT_OLD_VERSION\"" "version=\"$CRAFT_NEW_VERSION\"" ./setup.py
replace "VERSION = \"$CRAFT_OLD_VERSION\"" "VERSION = \"$CRAFT_NEW_VERSION\"" ./sentry_sdk/consts.py
replace "release = \"$CRAFT_OLD_VERSION\"" "release = \"$CRAFT_NEW_VERSION\"" ./docs/conf.py
