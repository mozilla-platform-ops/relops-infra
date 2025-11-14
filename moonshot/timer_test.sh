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

echo "start"
countdown 4
echo "end"