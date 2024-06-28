#!/usr/bin/env bash

set -e

export TASKCLUSTER_ROOT_URL="https://firefox-ci-tc.services.mozilla.com/"

. ./venv/bin/activate


#ci-admin check --environment firefoxci
ci-admin generate --json --environment firefoxci > generated.json
