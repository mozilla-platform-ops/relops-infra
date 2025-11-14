#!/usr/bin/env bash

set -e
# set -x

# TODO: handle override file generation somehow... gross currently.
#  - deliver.sh shouldn't use implicit path, have to pass in path for override

# user settings
ROLE="gecko_t_linux_2404_talos"
PUPPET_BRANCH="110425_2404_errata_2_including_ntp"
RONIN_PUPPET_REPO_PATH="/Users/aerickson/git/ronin_puppet"


########
######## non-user editable section below ########
########

CHASSIS="$1"
CARTRIDGE="$2"
HOST_NUMBER="$3"
HOSTNAME="t-linux64-ms-${HOST_NUMBER}.test.releng.mdc1.mozilla.com"

# heredoc for ascii art
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

# show all of the options we are using
echo "CHASSIS:                        $CHASSIS"
echo "CARTRIDGE:                      $CARTRIDGE"
echo "HOST_NUMBER:                    $HOST_NUMBER"
echo "ROLE:                           $ROLE"
echo "PUPPET_BRANCH:                  $PUPPET_BRANCH"
echo "HOSTNAME (uses HOST_NUMBER):    $HOSTNAME"
echo ""
# confirm with user before proceeding
read -p "Proceed with reimage and converge of ${HOSTNAME}? (y/n) " -n 1 -r
echo    # move to a new line
if [[ ! $REPLY =~ ^[Yy]$ ]] ; then
    echo "Aborting per user request."
    exit 1
fi

set -x

# reimage the host
echo "Reimaging chassis ${CHASSIS} cartridge ${CARTRIDGE}..."
./reimage_2404.sh "$CHASSIS" "$CARTRIDGE"
echo ""
echo "Reimaging complete."

# sleep 10 minutes to allow the host to finish reinstalling
echo "Sleeping 10 minutes to allow host to finish reinstalling..."
sleep 600

# deliver the boostrap script to the host
#   e.g. ./deliver_linux.sh t-linux64-ms-023.test.releng.mdc1.mozilla.com gecko_t_linux_2404_talos
echo "Delivering bootstrap script to host..."
${RONIN_PUPPET_REPO_PATH}/provisioners/linux/deliver_linux.sh "${HOSTNAME}" "${ROLE}"

# run the script to converge the host
echo "Running bootstrap script on host to converge..."
ssh relops@"${HOSTNAME}" "sudo bash -c \"PUPPET_REPO='https://github.com/aerickson/ronin_puppet.git' PUPPET_BRANCH='${PUPPET_BRANCH}' /tmp/bootstrap.sh\""

echo "Reimage and converge of ${HOSTNAME} complete."