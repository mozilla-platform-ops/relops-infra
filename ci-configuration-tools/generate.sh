#!/usr/bin/env bash

set -e
set -x

export TASKCLUSTER_ROOT_URL="https://firefox-ci-tc.services.mozilla.com/"

GITHUB_TOKEN=""
# source the ~/.github_token file if it exists, use it's value in GH_TOKEN
TOKEN_FILE="$HOME/.github_token_powderdry-cli-2"
if [ -f $TOKEN_FILE ]; then
    GITHUB_TOKEN=$(cat $TOKEN_FILE)
fi
export GITHUB_TOKEN


#. ./venv/bin/activate

# TODO: also requires `pip install -e .`
#   - do it or warn
#   - file issue to move to poetry?

#ci-admin check --environment firefoxci
uv run ci-admin generate --json --environment firefoxci > generated.json
