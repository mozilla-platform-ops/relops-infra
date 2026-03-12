#!/usr/bin/env python3

import argparse
import sys
import requests

from moonshot_lib import expand_host, normalize_node, make_headers, load_credentials, send_reboot, print_success

# uses HTTP requests to reboot a Moonshot node via iLO Redfish API

# example usage:
#   ./reset_moonshot.py \
#     -H moon-chassis-7.inband.releng.mdc1.mozilla.com \
#     -n c5n1

# TODO: be able to specify multiple nodes to cold boot

def parse_args():
    parser = argparse.ArgumentParser(
        description="Reboot a Moonshot node via iLO Redfish API"
    )
    parser.add_argument("-H", "--host", required=True, help="iLO host (an integer can be specified that will expand to moon-chassis-X.inband.releng.mdc1.mozilla.com)")
    parser.add_argument("-n", "--node", required=True, help="Node ID (e.g., c1n1, c1, or 1 — n1 is assumed if omitted)")
    parser.add_argument("-f", "--force", action="store_true", help="Force the operation without confirmation")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show request and response details")
    return parser.parse_args()


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
        print(f"This will reboot node:")
        print(f"  {args.node} @ {args.host}")
        confirm = input("Are you sure you want to proceed? (y/N) ")

        if confirm.lower() != "y":
            print("Operation cancelled.")
            sys.exit(0)

    send_reboot(system_url, headers, verbose=args.verbose)

    print_success("Reboot command sent successfully.")


if __name__ == "__main__":
    # Disable warnings about unverified HTTPS requests
    requests.packages.urllib3.disable_warnings()
    main()
