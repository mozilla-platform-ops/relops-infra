#!/usr/bin/env bash

#set -x
set -e

cat generated.json| jq '.resources[] | select(.kind == "WorkerPool") | .config.launchConfigs[].disks[].initializeParams.sourceImage' 2>/dev/null | grep -v null | sort | uniq -c | sort
