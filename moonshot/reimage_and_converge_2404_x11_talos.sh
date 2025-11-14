#!/usr/bin/env bash

set -e
# set -x

# oneshot script:
#   reimages and converges a linux moonshot hardware host

# TODO: handle override file generation somehow... gross currently.
#  - deliver.sh shouldn't use implicit path, have to pass in path for override

# user settings
# TODO: take this via cli args?
ROLE="gecko_t_linux_2404_talos"
RONIN_PUPPET_REPO_PATH="/Users/aerickson/git/ronin_puppet"


########
######## non-user editable section below ########
########


# functions

# v4 countdown function with spinner
countdown() {
    local total="$1"
    local spin='-\|/'
    local i=0

    for ((remaining=total; remaining>-1; remaining--)); do
        for ((j=0; j<10; j++)); do
            printf "\rWaiting $total seconds. Time remaining $remaining seconds... ${spin:$((j%4)):1}"
            sleep 0.1
        done
    done
    echo ""
}

# main

CHASSIS="$1"
CARTRIDGE="$2"
HOST_NUMBER="$3"
HOSTNAME="t-linux64-ms-${HOST_NUMBER}.test.releng.mdc1.mozilla.com"

# TODO: show usage if any args are missing
if [[ -z "$CHASSIS" || -z "$CARTRIDGE" || -z "$HOST_NUMBER" ]]; then
  echo "Usage: $0 <chassis> <cartridge> <host_number>"
  echo "Example: $0 1 3 023"
  exit 1
fi

# heredoc for ascii art (fonts are default (nothing specified) and 'slant')
cat << "EOF"
  ___                  _           _   _
 / _ \ _ __   ___  ___| |__   ___ | |_| |
| | | | '_ \ / _ \/ __| '_ \ / _ \| __| |
| |_| | | | |  __/\__ \ | | | (_) | |_|_|
 \___/|_| |_|\___||___/_| |_|\___/ \__(_)
         ___  __ __   ____  __ __
        |__ \/ // /  / __ \/ // /
        __/ / // /_ / / / / // /_
       / __/__  __// /_/ /__  __/
      /____/ /_/ (_)____/  /_/

EOF

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

# confirm with user before proceeding
read -p "Proceed with reimage and converge of ${HOSTNAME}? (y/N) " -n 1 -r
echo ""  # move to a new line
if [[ ! "$REPLY" =~ ^[Yy]$ ]] ; then
    echo "Aborting per user request."
    exit 1
fi

set -x

# reimage the host
echo "Reimaging chassis ${CHASSIS} cartridge ${CARTRIDGE}..."
./reimage_2404.sh "${CHASSIS}" "${CARTRIDGE}"
echo ""
echo "Reimaging complete."

# sleep 10 minutes to allow the host to finish installation
echo "Sleeping 10 minutes to allow host to finish OS installation..."
countdown 600

# deliver the bootstrap script to the host
#   e.g. ./deliver_linux.sh t-linux64-ms-023.test.releng.mdc1.mozilla.com gecko_t_linux_2404_talos
echo "Delivering bootstrap script to host..."
${RONIN_PUPPET_REPO_PATH}/provisioners/linux/deliver_linux.sh "${HOSTNAME}" "${ROLE}"

# run the script to converge the host
echo "Running bootstrap script on host to converge..."
# if PUPPET_REPO and PUPPET_BRANCH are defined, run this
if [[ -n "$PUPPET_REPO" && -n "$PUPPET_BRANCH" ]]; then
  ssh relops@"${HOSTNAME}" sudo bash -c "PUPPET_REPO='${PUPPET_REPO}' PUPPET_BRANCH='${PUPPET_BRANCH}' /tmp/bootstrap.sh"
else
  ssh relops@"${HOSTNAME}" sudo bash -c "/tmp/bootstrap.sh"
fi

echo "Reimage and converge of ${HOSTNAME} complete."