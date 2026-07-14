#!/usr/bin/env bash

set -e

export TASKCLUSTER_ROOT_URL="https://firefox-ci-tc.services.mozilla.com/"

# venv-based
#
. ./venv/bin/activate

# slower, more complete
# ci-admin check --environment firefoxci

# faster
ci-admin check --environment firefoxci --resources worker_pools
