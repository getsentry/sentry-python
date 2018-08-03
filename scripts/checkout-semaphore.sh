#!/bin/sh

# This script is able to restore partially checked out repositories,
# repositories that have all files but no git content, repositories checked out
# on the wrong branch, etc.
#
# This is mainly useful because Travis creates an empty folder in the attempt
# to restore the build cache.

set -xe

if [ ! -d checkouts/semaphore/.git ]; then
    mkdir -p checkouts/semaphore/
fi

cd checkouts/semaphore/
git init
git remote remove origin || true
git remote add origin https://github.com/getsentry/semaphore
git fetch
git reset --hard origin/master
git clean -f
cargo build
