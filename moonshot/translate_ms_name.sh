#!/usr/bin/env bash

hostname_prefix="t-linux64-ms-"
workerType="gecko-t-linux-talos"
check_hostname=${1}

for c in {1..14}; do
    dc=$(( c / 8 + 1 ))
    nstart=$(( (c - 1) * 45 ))
    if (( dc > 1 )); then
        # The last chassis in mdc1 has only 30 cartridges installed
        # but we continued numbering from 300
        nstart=$(( nstart - 15 ))
    fi
    # For linux, we are using the first 15 on each chassis.
    for i in {1..15}; do
        I=$(( nstart + i ))
        if ! (( c % 7 )) && (( i > 10 )); then
            break
        fi
        hostname=${hostname_prefix}$(printf "%03g" "${I}")
        if [[ $hostname = $check_hostname ]] || [[ $hostname = ${hostname_prefix}${check_hostname} ]]; then
            echo "${hostname}.test.releng.mdc${dc}.mozilla.com https://moon-chassis-${c}.inband.releng.mdc${dc}.mozilla.com/#/node/show/overview/r/rest/v1/Systems/c${i}n1"
            echo "--hostname Administrator@moon-chassis-${c}.inband.releng.mdc${dc}.mozilla.com --addr c${i}n1" >&2
            url="https://queue.taskcluster.net/v1/provisioners/releng-hardware/worker-types/${workerType}/workers/mdc${dc}/${hostname}"
            if wget -q -O - "${url}" >/dev/null 2>&1 ; then
                echo "https://tools.taskcluster.net/provisioners/releng-hardware/worker-types/${workerType}/workers/mdc${dc}/${hostname}"
            else
                echo "Worker not found: ${url}"
            fi
            exit 0
        fi
    done
done
