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

# countdown3 function with spinner
countdown3() {
    local total="$1"
    local spin='-\|/'
    local i=0

    for ((remaining=total; remaining>-1; remaining--)); do
        for ((j=0; j<4; j++)); do
            printf "\rWaiting $total seconds. Time remaining $remaining seconds... ${spin:$j:1}"
            sleep 0.25
        done
    done
    echo ""
}

# TODO: countdown4( like v3, but higher frequency spinner updates)
countdown4() {
    local total="$1"
    local spin='-\|/'
    local i=0

    for ((remaining=total; remaining>-1; remaining--)); do
        for ((j=0; j<10; j++)); do
            printf "\rWaiting $total seconds. Time remaining $remaining seconds... ${spin:$((j%4)):1}"
            sleep 0.1
        done
    done
    echo ""
}

echo "start"
countdown4 4
echo "end"