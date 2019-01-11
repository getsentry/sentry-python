#!/bin/bash

set -e

if [ "$TRAVIS" = true ] && [ -z "$GITHUB_API_TOKEN" ]; then
    echo "Not running on external pull request"
    exit 0;
fi

target=semaphore

# Download the latest semaphore release for Travis

output="$(
    curl -s \
    https://api.github.com/repos/getsentry/semaphore/releases/latest?access_token=$GITHUB_API_TOKEN
)"

echo "$output"

output="$(echo "$output" \
    | grep "$(uname -s)" \
    | grep "download" \
    | cut -d : -f 2,3 \
    | tr -d , \
    | tr -d \")"

echo "$output" | wget -i - -O $target
[ -s $target ]
chmod +x $target
