#!/usr/bin/env bash

hostname_prefix="t-linux64-ms-"
workerType="gecko-t-linux-talos"

for c in {1..14}; do
    dc=$(( c / 7 + 1 ))
    nstart=$(( (c - 1) * 45 ))
    if (( dc > 1 )); then
        # The last chassis in mdc1 has only 30 cartridges installed
        # but we continued numbering from 300
        nstart=$(( nstart - 15 ))
        # Do not touch the last chassis mdc2. It is for testing.
        if (( c == 14 )); then
            continue
        fi
    fi
    echo $c" "$nstart
    # For linux, we are using the first 15 on each chassis.
    for i in {1..15}; do
        I=$(( nstart + i ))
        if ! (( c % 7 )) && (( i > 9 )); then
            break
        fi
        hostname=${hostname_prefix}$(printf "%03g" "${I}")
        url="https://queue.taskcluster.net/v1/provisioners/releng-hardware/worker-types/${workerType}/workers/mdc${dc}/${hostname}"
        wget -q -O - "${url}" >/dev/null 2>&1 \
            || echo "${I} ${url} : --hostname Administrator@moon-chassis-${c}.inband.releng.mdc${dc}.mozilla.com --addr c${i}n1"
    done
done | tee moonshot_taskcluster_state.$(date +"%H:%M:%S").log