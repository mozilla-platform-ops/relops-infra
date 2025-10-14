#!/usr/bin/env bash

set -e
set -x

HOST=$1

if [ -z "$HOST" ]; then
  echo "Usage: $0 <host>"
  exit 1
fi

# TODO: prevent generic-worker from running once enough disk is free?
# - `pkill run-puppet.sh`? no, reboots host
# ssh "$HOST" sudo pkill run-puppet.sh
ssh "$HOST" sudo pkill run-start-worker-wrapper.sh || true

# store the script in a variable
SCRIPT=$(cat <<'EOF'
set -x
set -e

echo "Cleaning up build caches..."
sudo rm -rf /home/ctlbld/.mozbuild /home/cltbld/caches /home/cltbld/file-caches.json

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