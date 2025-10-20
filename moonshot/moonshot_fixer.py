#!/usr/bin/env python3

import sys
import subprocess
import argparse
from shutil import which

# Use argparse for argument parsing
parser = argparse.ArgumentParser(description="Fix moonshot host disk issues.")
parser.add_argument("host_number", type=str, help="Moonshot host number (e.g. 001)")
parser.add_argument('-f', '--force', action='store_true', help="Force operation without confirmation")
args = parser.parse_args()

host = f"t-linux64-ms-{args.host_number}.test.releng.mdc1.mozilla.com"

# Show host and get user confirmation
print(f"Host: {host}")
reply = input("Is this correct? (y/n) ").strip().lower()
if reply != 'y':
    print("Aborting.")
    sys.exit(1)
print("User has confirmed. Continuing...")

# do a ping check, 5 second timeout
try:
    subprocess.check_output(["ping", "-c", "1", host], timeout=5)
    print("Ping check successful.")
except subprocess.CalledProcessError:
    print("Ping check failed.")
    sys.exit(1)

# if forced, skip the disk check
if args.force:
    print("Skipping disk usage check due to --force flag.")
else:
    print("Checking disk usage on host...")
    try:
        output = subprocess.check_output([
            "ssh", host, "df -h | grep vg-root"
        ], text=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to get disk usage: {e}")
        sys.exit(1)

    try:
        percent_used = int([
            x for x in output.split() if x.endswith('%')
        ][0].strip('%'))
    except Exception:
        print("Could not parse disk usage.")
        sys.exit(1)

    if percent_used < 70:
        print("Disk usage is below 70%, no need to clean up.")
        sys.exit(0)
    print(f"Disk usage is above 70% ({percent_used}%), proceeding with cleanup...")

# Prevent generic-worker from running
subprocess.run([
    "ssh", host, "sudo pkill run-start-worker-wrapper.sh"
], check=False)

# Remote cleanup script
remote_script = '''
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
'''

proc = subprocess.run([
    "ssh", host, "bash -s"
], input=remote_script, text=True)

print(f"Please reboot the host with:\n  ssh {host} sudo reboot")

# If `gong` is present, use it
from shutil import which
if which("gong"):
    subprocess.run(["gong"])
