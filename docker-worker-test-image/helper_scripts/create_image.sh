#!/bin/bash

set -x
set -e

# vm to capture
vm_name="aerickson-dw-tester-build-240614 "

# shouldn't have to change below here

# like docker-worker-gcp-u14-04-2024-03-25
date_str=$(date +%Y-%m-%d)
image_name="docker-worker-gcp-u14-04-$date_str"

gcloud compute images create $image_name \
  --project=taskcluster-imaging --source-disk=$vm_name \
  --source-disk-zone=us-west1-b --storage-location=us

