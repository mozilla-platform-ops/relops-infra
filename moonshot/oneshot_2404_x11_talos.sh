#!/usr/bin/env bash

set -e
# set -x

CHASSIS="$1"
CARTRIDGE="$2"
HOST_NUMBER="$3"

# check that all are set
if [[ -z "$CHASSIS" || -z "$CARTRIDGE" || -z "$HOST_NUMBER" ]]; then
  echo "Usage: $0 <chassis> <cartridge> <host_number>"
  echo "Example: $0 1 3 023"
  exit 1
fi

# export ROLE="gecko_t_linux_2404_talos"
./oneshot_linux.sh "$CHASSIS" "$CARTRIDGE" "$HOST_NUMBER" "gecko_t_linux_2404_talos" "$@"