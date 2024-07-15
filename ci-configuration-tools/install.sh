#!/usr/bin/env bash

set -e
set -x

SCRIPT_PATH="$(realpath "${BASH_SOURCE[0]}")"

#./make_symlinks_for_executables.py --ignore "${SCRIPT_PATH}" /Users/aerickson/hg/ci-configuration
./make_symlinks_for_executables.py --ignore "${SCRIPT_PATH}" /Users/aerickson/git/fxci-config
