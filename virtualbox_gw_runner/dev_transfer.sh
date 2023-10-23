#!/usr/bin/env bash

set -e
# set -x

#
#
# dev_transfer.sh
#   - used to transfer virtualbox_gw_runner to a virtualbox host when developing.
#
#

# select the correct instance
#
# GCP_VM_NAME='wayland-2204-test-vm-7-vtx-enabled'
# GCP_VM_NAME='wayland-2204-test-vm-8-autoscale-dev'
GCP_VM_NAME='wayland-2204-test-vm-9-mesa-lib-fix'

echo "GCP instance: ${GCP_VM_NAME}..."

########################################################
# no user options from here on
########################################################

FINAL_DIR=/home/ubuntu/.virtualbox_gw_runner

VM_ZONE="$(gcloud compute instances list --filter=name=$GCP_VM_NAME --format 'csv[no-heading](zone)')"
HOST="$(gcloud compute instances describe ${GCP_VM_NAME} --project 'translations-sandbox' --zone=${VM_ZONE} --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"
# host is now fetched from gcp api query (see above)
# HOST="35.230.98.86"

# for debugging
# exit 0

# TODO: ensure using recent version of rsync
#   - brew install rsync

# generate sha.txt
repo_sha=$(git describe --always --dirty --match=NeVeRmAtCh 2>&1)
echo "$repo_sha" > sha.txt

# tranfer everything
rsync -avzh --stats --progress --delay-updates --chown=ubuntu:ubuntu \
  --rsync-path="sudo -A rsync"  --delete --delete-excluded \
  --exclude=.venv --exclude=misc --exclude=dev_transfer.sh --exclude=.gitignore \
  ./ ${HOST}:${FINAL_DIR}

echo ""
echo "SUCCESS. deployed $repo_sha."
