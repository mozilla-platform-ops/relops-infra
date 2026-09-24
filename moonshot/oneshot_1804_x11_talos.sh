#!/usr/bin/env bash

set -e
# set -x

CONFIRM=false
RONIN_SETTINGS_PATH=""
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
  arg="${1//$'\u00a0'/}"
  case "$arg" in
    --confirm)
      CONFIRM=true
      shift
      ;;
    --ronin-settings)
      if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "Error: --ronin-settings requires a file path." >&2
        exit 1
      fi
      RONIN_SETTINGS_PATH="$2"
      shift 2
      ;;
    --*)
      echo "Error: unknown option: $arg" >&2
      exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -n "$RONIN_SETTINGS_PATH" && ( ! -f "$RONIN_SETTINGS_PATH" || ! -r "$RONIN_SETTINGS_PATH" ) ]]; then
  echo "Error: ronin_settings file is not readable: $RONIN_SETTINGS_PATH" >&2
  exit 1
fi

SETTINGS_ARGS=()
if [[ -n "$RONIN_SETTINGS_PATH" ]]; then
  SETTINGS_ARGS=(--ronin-settings "$RONIN_SETTINGS_PATH")
fi

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
  echo "Usage: $0 <host_number_or_hostname> [--ronin-settings <path>] [--confirm]"
  echo "   or: $0 <chassis> <cartridge> <host_number> [--ronin-settings <path>] [--confirm]"
  echo "Example: $0 229 --confirm"
  echo ""
  echo "Note: --confirm flag is required to execute. Without it, shows dry run."
  echo "Set SKIP_REIMAGE=1 to skip reimaging and only converge the host."
  exit 1
fi

if [[ ! "$HOST_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Error: host_number must be the numeric worker ID (for example, 046), not a hostname or FQDN." >&2
  exit 1
fi

ROLE="gecko_t_linux_talos"
OS_VERSION="1804"
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
  echo "Set SKIP_REIMAGE=1 to skip reimaging and only converge the host."
  echo ""
  if [[ -n "${SKIP_REIMAGE:-}" ]]; then
    echo "Would skip reimage and converge:"
  else
    echo "Would reimage and converge:"
  fi
  echo "  Hostname:     $HOSTNAME"
  echo "  Chassis:      $CHASSIS"
  echo "  Cartridge:    $CARTRIDGE"
  echo "  Host Number:  $HOST_NUMBER"
  echo "  OS Version:   Ubuntu $OS_VERSION (18.04)"
  echo "  Puppet Role:  $ROLE"
  echo "  Settings:     ${RONIN_SETTINGS_PATH:-<none>}"
  echo ""
  echo "Command that would run:"
  printf '  '
  if [[ -n "${SKIP_REIMAGE:-}" ]]; then
    printf 'SKIP_REIMAGE=%q ' "$SKIP_REIMAGE"
  fi
  printf './oneshot_linux.sh %q %q %q %q %q' "$CHASSIS" "$CARTRIDGE" "$HOST_NUMBER" "$ROLE" "$OS_VERSION"
  if [[ -n "$RONIN_SETTINGS_PATH" ]]; then
    printf ' --ronin-settings %q' "$RONIN_SETTINGS_PATH"
  fi
  printf '\n'
  echo ""
  echo "To execute, run:"
  printf '  '
  if [[ -n "${SKIP_REIMAGE:-}" ]]; then
    printf 'SKIP_REIMAGE=%q ' "$SKIP_REIMAGE"
  fi
  printf '%q %s' "$0" "$EXECUTE_ARGS"
  if [[ -n "$RONIN_SETTINGS_PATH" ]]; then
    printf ' --ronin-settings %q' "$RONIN_SETTINGS_PATH"
  fi
  printf ' --confirm\n'
  exit 0
fi

# export ROLE="gecko_t_linux_talos"
./oneshot_linux.sh "$CHASSIS" "$CARTRIDGE" "$HOST_NUMBER" "$ROLE" "$OS_VERSION" "${SETTINGS_ARGS[@]}"
