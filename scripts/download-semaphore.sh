#!/bin/sh

# Download the latest semaphore release for Travis

curl -s https://api.github.com/repos/getsentry/semaphore/releases/latest \
    | grep "Linux" \
    | cut -d : -f 2,3 \
    | tr -d \" \
    | wget -i - -O ./semaphore

chmod +x ./semaphore
