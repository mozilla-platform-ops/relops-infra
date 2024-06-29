#!/usr/bin/env bash

#set -x
set -e

# jq-based... misses things
# cat generated.json| jq '.resources[] | select(.kind == "WorkerPool") | .config.launchConfigs[].disks[].initializeParams.sourceImage' 2>/dev/null | grep -v null | sort | uniq -c | sort

# grep based
grep sourceImage generated.json | cut -f 2 -d ':' | sort | uniq -c | sort