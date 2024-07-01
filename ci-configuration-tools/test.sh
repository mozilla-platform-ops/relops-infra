#!/usr/bin/env bash

set -e

export TASKCLUSTER_ROOT_URL="https://firefox-ci-tc.services.mozilla.com/"

# venv-based
#
# . ./venv/bin/activate
# ci-admin check --environment firefoxci


# pipenv-based
#
pipenv run ci-admin check --environment firefoxci
