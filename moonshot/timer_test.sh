#!/usr/bin/env bash

set -e

# implemet countdown function
# it should:
#   - have a spinner every second and show the time remaining
#   - only use one line of output (ie. use \r to overwrite the line)
countdown() {
    local total="$1"
    sleep $total
}

countdown2() {
    local total="$1"
    for ((i=total; i>-1; i--)); do
        sleep 1
        printf "\rWaiting $total seconds. Time remaining $i seconds..."
    done
    echo ""
}

echo "start"
countdown2 4
echo "end"