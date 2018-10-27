#!/bin/bash

set -e

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
