#!/usr/bin/env bash

set -euo pipefail
# set -x
trap 'echo "Error at line $LINENO. Aborting."; exit 1' ERR

# oneshot script:
#   reimages and converges a linux moonshot hardware host

# TODO: handle override file generation somehow... gross currently.
#  - deliver.sh shouldn't use implicit path, have to pass in path for override

# user settings
RONIN_PUPPET_REPO_PATH="$HOME/git/ronin_puppet"


########
######## non-user editable section below ########
########


# functions

# TODO: just use pv for spinner?
#  e.g. `while true; do echo -n .; sleep 1; done | pv -s 10 -S -F '%t %p' > /dev/null`

# v4 countdown function with spinner
countdown() {
    local total="$1"
    local spin='-\|/'
    local i=0

    for ((remaining=total; remaining>-1; remaining--)); do
        for ((j=0; j<10; j++)); do
            printf "\rWaiting $total seconds. $remaining seconds remaining... ${spin:$((j%4)):1}"
            sleep 0.1
        done
    done
    echo ""
}

pv_countdown() {
    local total="$1"
    echo "Waiting $total seconds..."
    # Disable pipefail for this function to avoid pv pipe issues
    set +o pipefail
    ( while true; do echo -n .; sleep 1; done ) | pv -s "$total" -S -F '%t %p' > /dev/null
    set -o pipefail
}

run_remote_script() {
  local host="$1"
  local script="$2"
  if [[ -z "$host" || -z "$script" ]]; then
    echo "Usage: run_remote_script <host> <script.sh>"
    return 1
  fi

  # Create remote temp path (no pipeline; no subshell)
  local remote_tmp
  remote_tmp=$(ssh "$host" 'mktemp /tmp/remote_script.XXXXXX.sh')
  echo "Uploading and running on $host:$remote_tmp"

  # Stream the local script into the remote file, then run it
  # Read the local script in this shell to keep /dev/fd/* valid.
  ssh "$host" "cat > '$remote_tmp' && chmod +x '$remote_tmp' && bash '$remote_tmp'" < "$script"

  echo "Remote script kept at: $host:$remote_tmp"
}


# main

# args
CHASSIS="$1"
CARTRIDGE="$2"
HOST_NUMBER="$3"
ROLE="$4"
OS_VERSION="$5"

# calculated
HOSTNAME="t-linux64-ms-${HOST_NUMBER}.test.releng.mdc1.mozilla.com"

# TODO: show usage if any args are missing
if [[ -z "$CHASSIS" || -z "$CARTRIDGE" || -z "$HOST_NUMBER" || -z "$ROLE" || -z "$OS_VERSION" ]]; then
  echo "Usage: $0 <chassis> <cartridge> <host_number> <role> <os_version>"
  echo "Example: $0 1 3 023 gecko_t_linux_2404_talos 2404"
  exit 1
fi

# get the calling script's info for ascii art display
#
# 1. Get the Parent Process ID
CALLER_PID=$PPID
# 2. Get the full command line of the parent process.
#    -o command= : Specifies the output format to be the 'command' field.
#    The equals sign (=) suppresses the header line.
CALLER_FULL_CMD=$(ps -o command= -p $CALLER_PID)
# echo "** $CALLER_FULL_CMD **"
CALLING_SCRIPT_NAME=$(basename "$CALLER_FULL_CMD" | cut -d' ' -f1)
# TODO: check that caller had 'oneshot' in the name?
# echo "** Called from script: $CALLING_SCRIPT_NAME **"
CALLING_SNIPPET=$(echo "$CALLING_SCRIPT_NAME" | cut -f1 -d '.' | sed -e 's/^oneshot_//' -e 's/_/ /g')
# echo "** Calling snippet: $CALLING_SNIPPET **"
CALLING_SNIPPED_CAPITALIZED=$(echo "$CALLING_SNIPPET" | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')

# check if pyfiglet is available for ascii art
if uv tool run pyfiglet a >/dev/null 2>&1; then
    PYFIGLET_PRESENT=true
    pyfiglet() { uv tool run pyfiglet "$@"; }
elif command -v pyfiglet >/dev/null 2>&1; then
    PYFIGLET_PRESENT=true
else
    PYFIGLET_PRESENT=false
    echo "pyfiglet command not found, minimal-ascii-art mode enabled. :("
fi

# ensure that flock is available
if ! command -v flock >/dev/null 2>&1; then
  echo "flock command not found, please install it to proceed."
  echo "  on os x, install with 'brew install flock'"
  exit 1
fi

# heredoc for ascii art (fonts are default (nothing specified) and 'slant')
cat << "EOF"
  ___                  _           _   _
 / _ \ _ __   ___  ___| |__   ___ | |_| |
| | | | '_ \ / _ \/ __| '_ \ / _ \| __| |
| |_| | | | |  __/\__ \ | | | (_) | |_|_|
 \___/|_| |_|\___||___/_| |_|\___/ \__(_)

EOF

if [[ "$PYFIGLET_PRESENT" == true ]]; then
  pyfiglet -f slant "$CALLING_SNIPPED_CAPITALIZED" | sed 's/^/        /'
else
  echo ""
  echo "         ** $CALLING_SNIPPED_CAPITALIZED **"
  echo ""
fi

# if pv is present use pv_countdown, else use countdown
if command -v pv >/dev/null 2>&1; then
  # echo "pv command found, using pv_countdown for waits."
  countdown() {
    local total="$1"
    pv_countdown "$total"
  }
else
  echo "pv command not found, using built-in countdown for waits."
fi

# if there is a 'ronin-settings' file at RONIN_PUPPET_REPO_PATH/provisioners/linux,
#  then source it to override the defaults below
if [ -f "${RONIN_PUPPET_REPO_PATH}/provisioners/linux/ronin_settings" ]; then
  echo "Sourcing ronin-settings file to override default settings..."
  # shellcheck source=/dev/null
  source "${RONIN_PUPPET_REPO_PATH}/provisioners/linux/ronin_settings"
  # TODO: sort of dangerous, we don't fully control what comes in...
# else
#   echo "No ronin_settings file found at ${RONIN_PUPPET_REPO_PATH}/provisioners/linux/ronin_settings, using default settings."
fi

# show all of the options we are using
echo "CHASSIS:                        $CHASSIS"
echo "CARTRIDGE:                      $CARTRIDGE"
echo "HOST_NUMBER:                    $HOST_NUMBER"
echo "HOSTNAME (uses HOST_NUMBER):    $HOSTNAME"
echo "ROLE:                           $ROLE"
echo "PUPPET_REPO:                    ${PUPPET_REPO}"
echo "PUPPET_BRANCH:                  $PUPPET_BRANCH"
echo ""

# if skip_reimage is set, inform user
if [[ -n "${SKIP_REIMAGE:-}" ]]; then
  echo "NOTE: SKIP_REIMAGE is set; the reimage step will be skipped."
  echo ""
fi

#
if [ -f "${RONIN_PUPPET_REPO_PATH}/provisioners/linux/ronin_settings" ]; then
  echo "NOTE: ronin_settings file found. It will be sent out to hosts."
  echo ""
fi

# confirm with user before proceeding
read -p "Proceed? (y/N) " -n 1 -r || true
echo ""  # move to a new line
if [[ ! "${REPLY:-}" =~ ^[Yy]$ ]] ; then
    echo "Aborting per user request."
    exit 1
fi

if [[ -n "${SKIP_REIMAGE:-}" ]]; then
  echo "SKIP_REIMAGE is set; skipping reimage step."
else
  # Use flock to serialize reimage operations per chassis
  LOCK_FILE="/tmp/moonshot_chassis_${CHASSIS}.lock"
  
  echo "Acquiring lock for chassis ${CHASSIS}..."
  (
    flock -x 200
    echo "Lock acquired for chassis ${CHASSIS}."
    
    set -x
    # reimage the host
    echo "Reimaging chassis ${CHASSIS} cartridge ${CARTRIDGE}..."
    ./reimage_${OS_VERSION}.sh "${CHASSIS}" "${CARTRIDGE}"
    echo ""
    echo "Reimaging started."
    set +x
    
    echo "Lock released for chassis ${CHASSIS}."
  ) 200>"$LOCK_FILE"

  # sleep 10 minutes to allow the host to finish installation
  echo "Sleeping 10 minutes to allow host to finish OS installation..."
  countdown 600
  echo "Sleep complete."
fi

# check that the host is reachable via ssh (try forever until it works)
echo "Checking SSH connectivity to host..."
while ! nc -vz ${HOSTNAME} 22 >/dev/null 2>&1; do
  echo "SSH check for ${HOSTNAME} failed, retrying in 30 seconds..."
  countdown 30
done
echo "SSH connectivity to ${HOSTNAME} verified."
echo ""

# check that a simple ssh command works (try forever until it works)
while ! ssh -o BatchMode=yes -o ConnectTimeout=5 relops@"${HOSTNAME}" "echo 2>&1" && false; do
  echo "SSH command check for ${HOSTNAME} failed, retrying in 30 seconds..."
  countdown 30
done
echo "SSH command functionality to ${HOSTNAME} verified."
echo ""

set -x

# deliver the bootstrap script to the host
#   e.g. ./deliver_linux.sh t-linux64-ms-023.test.releng.mdc1.mozilla.com gecko_t_linux_2404_talos
echo "Delivering bootstrap script to host..."
cd ${RONIN_PUPPET_REPO_PATH}/provisioners/linux
./deliver_linux.sh "${HOSTNAME}" "${ROLE}"

read -r -d '' REMOTE_SCRIPT <<EOF || true
#!/usr/bin/env bash
set -e
sudo \\
  PUPPET_REPO=${PUPPET_REPO} \\
  PUPPET_BRANCH=${PUPPET_BRANCH} \\
  /tmp/bootstrap.sh
EOF

# run the script to converge the host
echo "Running bootstrap script on host to converge..."
# if PUPPET_REPO and PUPPET_BRANCH are defined, run this
if [[ -n "$PUPPET_REPO" && -n "$PUPPET_BRANCH" ]]; then
  run_remote_script "relops@${HOSTNAME}" <(echo "$REMOTE_SCRIPT")
else
  ssh relops@"${HOSTNAME}" sudo bash -c "/tmp/bootstrap.sh"
fi

set +x

if [[ "$PYFIGLET_PRESENT" == true ]]; then
  echo ""
  pyfiglet -f smslant "$HOST_NUMBER complete"
fi

echo ""
echo "Reimage and converge of ${HOSTNAME} complete."