#!/usr/bin/env bash

set -e

export TASKCLUSTER_ROOT_URL="https://firefox-ci-tc.services.mozilla.com/"

. ./venv/bin/activate

# TODO: also requires `pip install -e .`
#   - do it or warn
#   - file issue to move to poetry?

#ci-admin check --environment firefoxci
ci-admin generate --json --environment firefoxci > generated.json
