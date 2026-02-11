#!/usr/bin/env bash

set -e
# set -x

# Check for --confirm flag
CONFIRM=false
for arg in "$@"; do
  if [[ "$arg" == "--confirm" ]]; then
    CONFIRM=true
    break
  fi
done

CHASSIS="$1"
CARTRIDGE="$2"
HOST_NUMBER="$3"

# check that all are set
if [[ -z "$CHASSIS" || -z "$CARTRIDGE" || -z "$HOST_NUMBER" ]]; then
  echo "Usage: $0 <chassis> <cartridge> <host_number> --confirm"
  echo "Example: $0 1 3 023 --confirm"
  echo ""
  echo "Note: --confirm flag is required to execute. Without it, shows dry run."
  exit 1
fi

ROLE="gecko_t_linux_talos"
OS_VERSION="1804"
HOSTNAME="t-linux64-ms-${HOST_NUMBER}.test.releng.mdc1.mozilla.com"

# Dry run mode if --confirm not provided
if [[ "$CONFIRM" == false ]]; then
  echo "=== DRY RUN MODE ==="
  echo "This is a dry run. To execute, add --confirm flag."
  echo ""
  echo "Would reimage and converge:"
  echo "  Hostname:     $HOSTNAME"
  echo "  Chassis:      $CHASSIS"
  echo "  Cartridge:    $CARTRIDGE"
  echo "  Host Number:  $HOST_NUMBER"
  echo "  OS Version:   Ubuntu $OS_VERSION (18.04)"
  echo "  Puppet Role:  $ROLE"
  echo ""
  echo "Command that would run:"
  echo "  ./oneshot_linux.sh \"$CHASSIS\" \"$CARTRIDGE\" \"$HOST_NUMBER\" \"$ROLE\" \"$OS_VERSION\""
  echo ""
  echo "To execute, run:"
  echo "  $0 $CHASSIS $CARTRIDGE $HOST_NUMBER --confirm"
  exit 0
fi

# export ROLE="gecko_t_linux_talos"
./oneshot_linux.sh "$CHASSIS" "$CARTRIDGE" "$HOST_NUMBER" "$ROLE" "$OS_VERSION"
