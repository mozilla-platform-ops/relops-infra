#!/usr/bin/env python3

import argparse
import base64
import json
import sys
import requests

# uses HTTP requests to set PXE boot and reboot a Moonshot node via iLO Redfish API

# example usage:
#   ./reset_moonshot_and_pxe_boot.py \
#     -H moon-chassis-7.inband.releng.mdc1.mozilla.com \
#     -n c5n1

def parse_args():
    parser = argparse.ArgumentParser(
        description="Set PXE boot and reboot a Moonshot node via iLO Redfish API"
    )
    parser.add_argument("-H", "--host", required=True, help="iLO host (an integer can be specified that will expand to moon-chassis-X.inband.releng.mdc1.mozilla.com)")
    parser.add_argument("-n", "--node", required=True, help="Node ID (e.g., c1n1)")
    parser.add_argument("-f", "--force", action="store_true", help="Force the operation without confirmation")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show request and response details")
    return parser.parse_args()

def make_headers(username, password):
    creds = f"{username}:{password}"
    encoded = base64.b64encode(creds.encode("ascii")).decode("ascii")
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json"
    }

def set_pxe_boot(system_url, headers, verbose=False):
    payload = {
        "Boot": {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "PXE"
        }
    }
    print(f"Setting PXE boot via PATCH {system_url}...")
    if verbose:
        print(f"[VERBOSE] PATCH {system_url}")
        print(f"[VERBOSE] Headers: {headers}")
        print(f"[VERBOSE] Payload: {json.dumps(payload)}")
    response = requests.patch(system_url, headers=headers, json=payload, verify=False)
    if verbose:
        print(f"[VERBOSE] Response: {response.status_code} {response.text}")
    if not response.ok:
        print(f"[ERROR] Failed to set PXE boot: {response.status_code} {response.text}")
        sys.exit(1)

def send_reboot(system_url, headers, verbose=False):
    payload = {
        "Action": "Reset",
        "ResetType": "ColdReset"
    }
    print(f"Sending ColdReset reboot request via POST {system_url}...")
    if verbose:
        print(f"[VERBOSE] POST {system_url}")
        print(f"[VERBOSE] Headers: {headers}")
        print(f"[VERBOSE] Payload: {json.dumps(payload)}")
    response = requests.post(system_url, headers=headers, json=payload, verify=False)
    if verbose:
        print(f"[VERBOSE] Response: {response.status_code} {response.text}")
    if not response.ok:
        print(f"[ERROR] Failed to send reboot: {response.status_code} {response.text}")
        sys.exit(1)

def main():
    args = parse_args()

    # if args.host is an int, then expand to full hostname
    if args.host.isdigit():
        old_host = args.host
        args.host = f"moon-chassis-{args.host}.inband.releng.mdc1.mozilla.com"
        print(f"Expanding host {old_host} to full hostname {args.host}...")

    system_url = f"https://{args.host}/rest/v1/Systems/{args.node}"

    # load username and password from ~/.moonshot_ilo
    try:
        with open("/Users/aerickson/.moonshot_ilo", "r") as f:
            lines = f.readlines()
            args.username = lines[0].strip()
            args.password = lines[1].strip()
    except Exception as e:
        print(f"[ERROR] Failed to load credentials: {e}")
        print("Please create a file at ~/.moonshot_ilo with your iLO username and password, one per line.")
        sys.exit(1)

    headers = make_headers(args.username, args.password)

    # double check that the user is ready to proceed
    if not args.force:
        print(f"This will set PXE boot and reboot node:")
        print(f"  {args.node} @ {args.host}")
        confirm = input("Are you sure you want to proceed? (y/N) ")

        if confirm.lower() != "y":
            print("Operation cancelled.")
            sys.exit(0)

    set_pxe_boot(system_url, headers, verbose=args.verbose)
    send_reboot(system_url, headers, verbose=args.verbose)

    print("\033[92mPXE boot set and reboot command sent successfully.\033[0m")

if __name__ == "__main__":
    # Disable warnings about unverified HTTPS requests
    requests.packages.urllib3.disable_warnings()
    main()

