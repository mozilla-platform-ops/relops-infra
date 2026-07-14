#!/usr/bin/env python3

import argparse
import re
import sys

import pexpect

from moonshot_lib import expand_host, hostname_to_cart, normalize_node, get_pyfiglet_output

# Opens an interactive VSP (Virtual Serial Port) console for a Moonshot cartridge
# via SSH to the chassis iLO.
#
# example usage (worker hostname):
#   ./vsp_console.py t-linux64-ms-001.test.releng.mdc1.mozilla.com
#   ./vsp_console.py t-linux64-ms-001
#
# example usage (direct mode):
#   ./vsp_console.py -H moon-chassis-3.inband.releng.mdc1.mozilla.com -n c5n1
#   ./vsp_console.py -H 3 -n 5


def parse_args():
    parser = argparse.ArgumentParser(
        description="Open an interactive VSP console for a Moonshot cartridge"
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "hostname",
        nargs="*",
        metavar="HOSTNAME",
        help="Worker hostname (e.g. t-linux64-ms-001); chassis and node are derived automatically",
    )

    direct = parser.add_argument_group("direct mode (specify chassis and node explicitly)")
    direct.add_argument("-H", "--host", help="iLO chassis host (an integer expands to moon-chassis-X.inband.releng.mdc1.mozilla.com)")
    direct.add_argument("-n", "--node", help="Node ID (e.g., c1n1, c1, or 1 — n1 is assumed if omitted)")

    args = parser.parse_args()

    if not args.hostname:
        if not args.host or not args.node:
            parser.error("provide a hostname, or both -H/--host and -n/--node")
    elif len(args.hostname) > 1:
        parser.error("VSP is interactive — only one hostname may be specified")

    return args


def build_target(args) -> tuple[str, str]:
    """Return (chassis_fqdn, node_id) for the target cartridge."""
    if args.hostname:
        result = hostname_to_cart(args.hostname)
        if not result:
            print(f"[ERROR] Could not determine chassis/node from: {args.hostname[0]}")
            sys.exit(1)
        chassis_fqdn, nodes = next(iter(result.items()))
        return chassis_fqdn, f"c{nodes[0]}n1"

    host = expand_host(args.host)
    node = normalize_node(args.node)
    if host != args.host:
        print(f"Expanding host {args.host} to {host}...")
    if node != args.node:
        print(f"Expanding node {args.node} to {node}...")
    return host, node


def main():
    args = parse_args()
    chassis_fqdn, node_id = build_target(args)

    chassis_num = re.search(r'moon-chassis-(\d+)', chassis_fqdn)
    cart_num = re.search(r'c(\d+)', node_id)
    if chassis_num and cart_num:
        print(get_pyfiglet_output(f"{chassis_num.group(1)}-{cart_num.group(1)}", font=["bigmono12", "ansi_shadow", "4max", "smmono12"]))

    print(f"Connecting to VSP console: {node_id} @ {chassis_fqdn}")
    print("To exit the VSP console, press: ESC (")
    print()

    child = pexpect.spawn(
        "ssh",
        ["-o", "StrictHostKeyChecking=no", f"relops@{chassis_fqdn}"],
        encoding="utf-8",
        timeout=30,
    )
    child.expect("hpiLO->")
    child.sendline(f"connect node vsp {node_id}")
    child.expect("Virtual Serial Port Active")

    try:
        child.interact()
    except KeyboardInterrupt:
        pass

    print("\nDisconnected.")


if __name__ == "__main__":
    main()
