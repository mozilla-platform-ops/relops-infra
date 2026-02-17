#!/bin/bash

set -e
# ensure that tee fails if the pipe fails
set -o pipefail

USAGE="Usage: $0 chassis node1 node2 ..\nExample: '\$ ./$0 7 {1..3}  # reinstall chassis 7 nodes 1-3\n\n"
if [ "$#" == "0" ]; then
    printf "$USAGE"
    exit 1
fi

chassis=$1
hostname=moon-chassis-${chassis}.inband.releng.mdc$(( chassis/8+1 )).mozilla.com
shift

# if we need to drop some secrets through the preseed, we could add them:
boot_params=""

MAX_RETRIES=3

for node in $@; do
    echo $node
    attempt=0
    while [ $attempt -lt $MAX_RETRIES ]; do
        attempt=$((attempt + 1))
        if ./reimage_2404.exp --chassis relops@${hostname} --node c${node}n1 --boot-params "$boot_params" \
                | tee ${0%%.sh}.$chassis.$node.$(date +"%H:%M:%S").log; then
            break
        fi
        echo "Attempt $attempt/$MAX_RETRIES failed."
        if [ $attempt -lt $MAX_RETRIES ]; then
            echo "Retrying in 5 seconds..."
            sleep 5
        else
            echo "All $MAX_RETRIES attempts failed for chassis $chassis node $node."
            exit 1
        fi
    done
done
