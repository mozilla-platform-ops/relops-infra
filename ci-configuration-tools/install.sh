#!/usr/bin/env bash

set -e
set -x

SCRIPT_PATH="$(realpath "${BASH_SOURCE[0]}")"

# TODO: cleanup install dir with:
#   gfind . -xtype l -delete
#
#   - otherwise create directory with all isntalled symlinks and remove those in target dir before installation every time

#./make_symlinks_for_executables.py --ignore "${SCRIPT_PATH}" /Users/aerickson/hg/ci-configuration
./make_symlinks_for_executables.py --ignore "${SCRIPT_PATH}" "${HOME}/git/fxci-config"
