#!/bin/sh
set -xe

if [ ! -d checkouts/semaphore ]; then
    mkdir -p checkouts
    git clone https://github.com/getsentry/semaphore ./checkouts/semaphore
fi

(
    cd ./checkouts/semaphore
    git checkout -f master
    git fetch
    git reset --hard origin/master
    cargo build
)
