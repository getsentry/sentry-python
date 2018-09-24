#!/bin/bash

set -xe

target=semaphore

# Download the latest semaphore release for Travis

output="$(curl -s https://api.github.com/repos/getsentry/semaphore/releases/latest)"

output="$(echo "$output" \
    | grep "Linux" \
    | grep "download" \
    | cut -d : -f 2,3 \
    | tr -d , \
    | tr -d \")"

echo "$output" | wget -i - -O $target
[ -s $target ]
chmod +x $target
