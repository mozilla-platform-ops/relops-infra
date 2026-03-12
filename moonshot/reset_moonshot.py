#!/usr/bin/env python3

import argparse
import sys
import requests

from moonshot_lib import expand_host, hostname_to_cart, normalize_node, make_headers, load_credentials, send_reboot, wait_for_online, worker_fqdn, print_success

# uses HTTP requests to reboot a Moonshot node via iLO Redfish API

# example usage (chassis + node):
#   ./reset_moonshot.py \
#     -H moon-chassis-7.inband.releng.mdc1.mozilla.com \
#     -n c5n1
#
# example usage (worker hostname):
#   ./reset_moonshot.py --hostname t-linux64-ms-001.test.releng.mdc1.mozilla.com
#   ./reset_moonshot.py --hostname t-linux64-ms-001 t-linux64-ms-002


def parse_args():
    parser = argparse.ArgumentParser(
        description="Reboot a Moonshot node via iLO Redfish API"
    )
    parser.add_argument("-f", "--force", action="store_true", help="Force the operation without confirmation")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show request and response details")
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for the node(s) to come back online after reboot")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--hostname",
        nargs="+",
        metavar="HOSTNAME",
        help="Worker hostname(s) (e.g. t-linux64-ms-001); chassis and node are derived automatically",
    )

    direct = parser.add_argument_group("direct mode (specify chassis and node explicitly)")
    direct.add_argument("-H", "--host", help="iLO chassis host (an integer expands to moon-chassis-X.inband.releng.mdc1.mozilla.com)")
    direct.add_argument("-n", "--node", help="Node ID (e.g., c1n1, c1, or 1 — n1 is assumed if omitted)")

    args = parser.parse_args()

    if args.hostname is None:
        if not args.host or not args.node:
            parser.error("-H/--host and -n/--node are both required when --hostname is not used")

    return args


def build_targets(args) -> list[tuple[str, str]]:
    """Return a list of (chassis_fqdn, node_id) pairs to reboot."""
    if args.hostname:
        targets = []
        for chassis_fqdn, nodes in hostname_to_cart(args.hostname).items():
            for node in nodes:
                targets.append((chassis_fqdn, f"c{node}n1"))
        return targets

    host = expand_host(args.host)
    node = normalize_node(args.node)
    if host != args.host:
        print(f"Expanding host {args.host} to {host}...")
    if node != args.node:
        print(f"Expanding node {args.node} to {node}...")
    return [(host, node)]


def main():
    args = parse_args()
    targets = build_targets(args)

    if not targets:
        print("[ERROR] No valid targets found.")
        sys.exit(1)

    username, password = load_credentials()
    headers = make_headers(username, password)

    if not args.force:
        print("This will reboot:")
        for host, node in targets:
            print(f"  {node} @ {host}")
        confirm = input("Are you sure you want to proceed? (y/N) ")
        if confirm.lower() != "y":
            print("Operation cancelled.")
            sys.exit(0)

    for host, node in targets:
        system_url = f"https://{host}/rest/v1/Systems/{node}"
        send_reboot(system_url, headers, verbose=args.verbose)

    print_success(f"Reboot command sent successfully ({len(targets)} node(s)).")

    if not args.no_wait:
        if args.hostname:
            print("Waiting for node(s) to come back online...")
            for fqdn in [worker_fqdn(h) for h in args.hostname]:
                wait_for_online(fqdn)
            print_success("All node(s) are back online.")
        else:
            print("(--no-wait not set, but wait is only supported in --hostname mode)")


if __name__ == "__main__":
    # Disable warnings about unverified HTTPS requests
    requests.packages.urllib3.disable_warnings()
    main()
