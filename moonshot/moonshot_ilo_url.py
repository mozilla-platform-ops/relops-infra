#!/usr/bin/env python3

import argparse
import sys

from moonshot_lib import hostname_to_cart

# Given a worker hostname (short or FQDN), print the iLO cartridge URL.
#
# example usage:
#   ./moonshot_ilo_url.py t-linux64-ms-214
#   ./moonshot_ilo_url.py t-linux64-ms-214.test.releng.mdc1.mozilla.com


def parse_args():
    parser = argparse.ArgumentParser(
        description="Print the iLO cartridge URL for a Moonshot worker"
    )
    parser.add_argument("hostname", nargs="+", help="Worker hostname(s) (short or FQDN)")
    return parser.parse_args()


def main():
    args = parse_args()
    cart_map = hostname_to_cart(args.hostname)

    if not cart_map:
        print("[ERROR] Could not resolve any hostnames to a chassis.", file=sys.stderr)
        sys.exit(1)

    for chassis_fqdn, nodes in cart_map.items():
        for node in nodes:
            url = f"https://{chassis_fqdn}/#/cartridge/show/overview/r/rest/v1/Chassis/1/Cartridges/C{node}"
            print(url)


if __name__ == "__main__":
    main()
