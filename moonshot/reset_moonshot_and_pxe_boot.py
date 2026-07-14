#!/usr/bin/env python3

import argparse
import json
import sys
import requests

from moonshot_lib import expand_host, normalize_node, make_headers, load_credentials, send_reboot, print_success

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
    parser.add_argument("-n", "--node", required=True, help="Node ID (e.g., c1n1, c1, or 1 — n1 is assumed if omitted)")
    parser.add_argument("-f", "--force", action="store_true", help="Force the operation without confirmation")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show request and response details")
    return parser.parse_args()


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


def main():
    args = parse_args()

    if args.host.isdigit():
        old_host = args.host
        args.host = expand_host(args.host)
        print(f"Expanding host {old_host} to full hostname {args.host}...")

    old_node = args.node
    args.node = normalize_node(args.node)
    if args.node != old_node:
        print(f"Expanding node {old_node} to {args.node}...")

    system_url = f"https://{args.host}/rest/v1/Systems/{args.node}"

    username, password = load_credentials()
    headers = make_headers(username, password)

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

    print_success("PXE boot set and reboot command sent successfully.")


if __name__ == "__main__":
    # Disable warnings about unverified HTTPS requests
    requests.packages.urllib3.disable_warnings()
    main()
