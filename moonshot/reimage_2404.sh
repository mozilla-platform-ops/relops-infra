#!/bin/bash

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

for node in $@; do
    echo $node
    ./reimage_2404.exp --chassis relops@${hostname} --node c${node}n1 --boot-params "$boot_params" \
        | tee ${0%%.sh}.$chassis.$node.$(date +"%H:%M:%S").log
done
