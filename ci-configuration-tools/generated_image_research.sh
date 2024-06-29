#!/usr/bin/env bash

set -e


## general investigation
# cat generated.json| jq '.resources' | grep sourceImage
# cat generated.json| jq '.resources[] | select(.kind == "WorkerPool")'

## finding sourceImage
# cat generated.json| jq '.resources[] | select(.kind == "WorkerPool") | .config.launchConfigs[].disks[].initializeParams.sourceImage'

## finding workerPoolId
# cat generated.json| jq '.resources[] | select(.kind == "WorkerPool") | .workerPoolId'
#
# how to format results / print multiple
# echo '{"key1": {"subkey1": "subvalue1", "subkey2": "subvalue2"}, "key2": "value2"}' | jq '{subkey1_value: .key1.subkey1, key2_value: .key2}'


# TODO: move this to jq or python
cat generated.json| grep -E 'sourceImage|workerPoolId'

