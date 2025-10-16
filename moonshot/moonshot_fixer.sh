#!/usr/bin/env bash

set -e
set -x

if [ -z "$1" ]; then
  echo "Usage: $0 <moonshot host number>"
  exit 1
fi
HOST=t-linux64-ms-$1.test.releng.mdc1.mozilla.com

# show host and get user to confirm it's correct
echo "Host: $HOST"
read -p "Is this correct? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborting."
  exit 1
fi
echo "User has confirmed. Continuing..."

echo "Checking disk usage on host..."
# check that the disk is full...
# look at df output for `vg-root`'s percentage field
OUTPUT=$(ssh "$HOST" df -h | grep vg-root)
# extract the percentage used
PERCENT_USED=$(echo "$OUTPUT" | awk '{print $5}' | tr -d '%')
if [ "$PERCENT_USED" -lt 70 ]; then
  echo "Disk usage is below 70%, no need to clean up."
  exit 0
fi
echo "Disk usage is above 70% ($PERCENT_USED%), proceeding with cleanup..."

# prevent generic-worker from running once enough disk is free?
# - `pkill run-puppet.sh`? no, reboots host
# ssh "$HOST" sudo pkill run-puppet.sh
ssh "$HOST" sudo pkill run-start-worker-wrapper.sh || true
# TOOD: this still doesn't prevent host from rebooting, find code in puppet and disable appropriately

# store the script in a variable
SCRIPT=$(cat <<'EOF'
set -x
set -e

echo "Cleaning up build caches..."
sudo rm -rf /home/ctlbld/.mozbuild \
            /home/cltbld/caches \
            /home/cltbld/file-caches.json \
            /home/cltbld/directory-caches.json

echo "Cleaning up apt/deb..."
sudo apt-get autoremove -y
sudo apt clean

echo "Fixing clock skew..."
sudo ntpd -q -g

echo "Showing date and disk usage..."
date
df -h
EOF
)

# execute the script
ssh "$HOST" bash -s <<ENDSSH
eval "$SCRIPT"
ENDSSH

# show command to reboot host?
echo "Please reboot the host with:"
echo "  ssh $HOST sudo reboot"

# if `gong` is present, use it
if command -v gong >/dev/null 2>&1; then
  gong
fi