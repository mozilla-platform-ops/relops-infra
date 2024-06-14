#!/usr/bin/env bash

set -e
set -x

# the base image to use/boot
IMAGENAME="docker-worker-gcp-u14-04-2024-03-25"

# shouldn't need to change below here

date_str=$(date +%y%m%d)
vm_name=$USER-dw-tester-build-$date_str

project=taskcluster-imaging
zone=us-west1-b
base_image=projects/taskcluster-imaging/global/images/$IMAGENAME
instance_type=n2-standard-2
name=$vm_name
DISKSIZE=75

gcloud compute instances create \
  "$name" \
  --project="$project" \
  --zone="$zone" \
  --machine-type="$instance_type" \
  --network-interface=network-tier=PREMIUM,subnet=default \
  --maintenance-policy=MIGRATE \
  --provisioning-model=STANDARD \
  --scopes=https://www.googleapis.com/auth/cloud-platform \
  --create-disk=auto-delete=yes,boot=yes,device-name="$name",image="$base_image",mode=rw,size=$DISKSIZE,type=projects/${project}/zones/"$zone"/diskTypes/pd-balanced \
  --local-ssd=interface=NVME \
  --enable-display-device \
  --metadata=startup-script-url=https://raw.githubusercontent.com/mozilla-platform-ops/relops-infra/master/gcp-scripts/killworker.sh
