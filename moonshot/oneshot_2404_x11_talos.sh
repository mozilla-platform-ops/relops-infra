#!/usr/bin/env bash

set -e
# set -x

# Check for --confirm flag
CONFIRM=false
for arg in "$@"; do
  # A non-breaking space is easy to paste after --confirm from formatted text.
  arg="${arg//$'\u00a0'/}"
  if [[ "$arg" == "--confirm" ]]; then
    CONFIRM=true
    break
  fi
done

POSITIONAL_ARGS=()
for arg in "$@"; do
  arg="${arg//$'\u00a0'/}"
  [[ "$arg" != "--confirm" ]] && POSITIONAL_ARGS+=("$arg")
done

if [[ ${#POSITIONAL_ARGS[@]} -eq 1 ]]; then
  # A worker number, short hostname, or FQDN is enough to locate a cartridge.
  # For example, 229 is chassis 6, cartridge 4.
  HOST_INPUT="${POSITIONAL_ARGS[0]}"
  HOST_PART="${HOST_INPUT%%.*}"
  if [[ "$HOST_PART" =~ ([0-9]+)$ ]]; then
    HOST_NUMBER=$(printf '%03d' "${BASH_REMATCH[1]}")
  else
    echo "Error: expected a Moonshot worker number or hostname, got: $HOST_INPUT" >&2
    exit 1
  fi

  HOST_ID=$((10#$HOST_NUMBER))
  if (( HOST_ID < 1 )); then
    echo "Error: worker ID must be positive, got: $HOST_NUMBER" >&2
    exit 1
  fi

  # Moonshot numbering has discontinuities at the MDC1/MDC2 boundary.
  if (( HOST_ID > 630 )); then
    CHASSIS=$(( ((HOST_ID - 1) - 30) / 45 + 2 ))
    CARTRIDGE=$(( ((HOST_ID - 1) - 630) % 45 + 1 ))
  elif (( HOST_ID > 615 )); then
    CHASSIS=$(( ((HOST_ID - 1) - 15) / 45 + 1 - 13 ))
    CARTRIDGE=$(( ((HOST_ID - 1) - 615) % 45 + 1 + 30 ))
  elif (( HOST_ID > 300 )); then
    CHASSIS=$(( ((HOST_ID - 1) + 15) / 45 + 1 ))
    CARTRIDGE=$(( ((HOST_ID - 1) + 15) % 45 + 1 ))
  else
    CHASSIS=$(( (HOST_ID - 1) / 45 + 1 ))
    CARTRIDGE=$(( (HOST_ID - 1) % 45 + 1 ))
  fi
  EXECUTE_ARGS="$HOST_INPUT"
elif [[ ${#POSITIONAL_ARGS[@]} -eq 3 ]]; then
  # Keep the original explicit form for callers and existing runbooks.
  CHASSIS="${POSITIONAL_ARGS[0]}"
  CARTRIDGE="${POSITIONAL_ARGS[1]}"
  HOST_NUMBER="${POSITIONAL_ARGS[2]}"
  EXECUTE_ARGS="$CHASSIS $CARTRIDGE $HOST_NUMBER"
else
  echo "Usage: $0 <host_number_or_hostname> [--confirm]"
  echo "   or: $0 <chassis> <cartridge> <host_number> [--confirm]"
  echo "Example: $0 229 --confirm"
  echo ""
  echo "Note: --confirm flag is required to execute. Without it, shows dry run."
  exit 1
fi

if [[ ! "$HOST_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Error: host_number must be the numeric worker ID (for example, 046), not a hostname or FQDN." >&2
  exit 1
fi

ROLE="gecko_t_linux_2404_talos"
OS_VERSION="2404"
if (( CHASSIS > 7 )); then
  DATACENTER="mdc2"
else
  DATACENTER="mdc1"
fi
HOSTNAME="t-linux64-ms-${HOST_NUMBER}.test.releng.${DATACENTER}.mozilla.com"

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
  echo "  OS Version:   Ubuntu $OS_VERSION (24.04)"
  echo "  Puppet Role:  $ROLE"
  echo ""
  echo "Command that would run:"
  echo "  ./oneshot_linux.sh \"$CHASSIS\" \"$CARTRIDGE\" \"$HOST_NUMBER\" \"$ROLE\" \"$OS_VERSION\""
  echo ""
  echo "To execute, run:"
  echo "  $0 $EXECUTE_ARGS --confirm"
  exit 0
fi

# export ROLE="gecko_t_linux_2404_talos"
./oneshot_linux.sh "$CHASSIS" "$CARTRIDGE" "$HOST_NUMBER" "$ROLE" "$OS_VERSION"
