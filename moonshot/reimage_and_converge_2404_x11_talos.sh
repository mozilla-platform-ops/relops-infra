#!/usr/bin/env bash

set -e
set -x

# TODO: handle override file generation somehow... gross currently.
#  - deliver.sh shouldn't use implicit path, have to pass in path for override

# user settings
ROLE="gecko_t_linux_2404_talos"
PUPPET_BRANCH="110425_2404_errata_2_including_ntp"
RONIN_PUPPET_REPO_PATH="/Users/aerickson/git/ronin_puppet"


######## non-user editable section below ########

CHASSIS="$1"
CARTRIDGE="$2"
HOST_NUMBER="$3"

HOSTNAME="t-linux64-ms-${HOST_NUMBER}.test.releng.mdc1.mozilla.com"

# reimage the host
./reimage_2404.sh "$CHASSIS" "$CARTRIDGE"

# sleep 10 minutes to allow the host to finish reinstalling
sleep 600

# deliver the boostrap script to the host
#   e.g. ./deliver_linux.sh t-linux64-ms-023.test.releng.mdc1.mozilla.com gecko_t_linux_2404_talos
${RONIN_PUPPET_REPO_PATH}/provisioners/linux/deliver_linux.sh "${HOSTNAME}" "${ROLE}"

# run the script to converge the host
ssh relops@"${HOSTNAME}" "sudo bash -c \"PUPPET_REPO='https://github.com/aerickson/ronin_puppet.git' PUPPET_BRANCH='${PUPPET_BRANCH}' /tmp/bootstrap.sh\""
