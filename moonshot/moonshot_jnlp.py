#!/usr/bin/env python3

import argparse
import os
import sys
import time
import requests

from moonshot_lib import (
    expand_host, hostname_to_cart, normalize_node, load_credentials,
    process_running, update_exception_sites, launch_javaws,
    wait_for_process_exit, remove_file, JAVAWS_STARTUP_SLEEP,
)

# Generate an iLO Java IRC JNLP file for a Moonshot cartridge.
#
# example usage (worker hostname):
#   ./moonshot_jnlp.py t-linux64-ms-214            # writes ~/Downloads/t-linux64-ms-214.jnlp
#   ./moonshot_jnlp.py t-linux64-ms-214 --stdout   # prints to stdout
#
# example usage (chassis + node):
#   ./moonshot_jnlp.py -H moon-chassis-5 -n c31n1

# IRC codebase port = cartridge_slot + 735 (e.g. slot 31 → port 766, slot 1 → port 736).
IRC_PORT_BASE = 735
# KVM port — hardcoded in iLO firmware JS.
KVM_PORT = 17988
# Jar filename — update if firmware changes the version suffix.
IRC_JAR = "intgapp4_231.jar"
# Static value embedded by iLO firmware JS — identical across chassis and sessions.
IRC_INFO0 = "7AC3BDEBC9AC64E85734454B53BB73CE"

JNLP_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8" ?>
<jnlp spec="1.0+" codebase="https://{chassis}:{irc_port}" href="">
  <information>
    <title>Integrated Remote Console</title>
    <vendor>HPE</vendor>
    <offline-allowed/>
  </information>
  <security>
    <all-permissions/>
  </security>
  <resources>
    <j2se version="1.5+" href="http://java.sun.com/products/autodl/j2se"/>
    <jar href="https://{chassis}:{irc_port}/html/{jar}" main="false" />
  </resources>
  <property name="deployment.trace.level property" value="basic"/>
  <applet-desc main-class="com.hp.ilo2.intgapp.intgapp" name="iLOJIRC"
      documentbase="https://{chassis}:{irc_port}/html/java_irc.html"
      width="1" height="1">
    <param name="RCINFO1" value="{rcinfo1}" />
    <param name="RCINFOLANG" value="en" />
    <param name="INFO0" value="{info0}" />
    <param name="INFO1" value="{kvm_port}" />
    <param name="INFO2" value="composite" />
  </applet-desc>
  <update check="background"/>
</jnlp>
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate an iLO Java IRC JNLP for a Moonshot cartridge"
    )
    parser.add_argument("--stdout", action="store_true", help="Print JNLP to stdout instead of writing to ~/Downloads/")
    parser.add_argument("--auto", action="store_true", help="Write JNLP, launch via javaws, wait for exit, then clean up. Mutually exclusive with --stdout.")
    parser.add_argument("--no-remove", action="store_true", help="With --auto: keep the JNLP after the console exits")
    parser.add_argument("--dry-run", action="store_true", help="With --auto: print actions instead of executing them")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "hostname",
        nargs="?",
        metavar="HOSTNAME",
        help="Worker hostname (e.g. t-linux64-ms-214); chassis and node are derived automatically",
    )

    direct = parser.add_argument_group("direct mode (specify chassis and node explicitly)")
    direct.add_argument("-H", "--host", help="iLO chassis host (integer expands to FQDN)")
    direct.add_argument("-n", "--node", help="Node ID (e.g., c31n1, c31, or 31)")

    args = parser.parse_args()

    if args.hostname is None and not (args.host and args.node):
        parser.error("provide a worker hostname, or both -H/--host and -n/--node")
    if args.auto and args.stdout:
        parser.error("--auto and --stdout are mutually exclusive")

    return args


def build_target(args) -> tuple[str, str]:
    """Return (chassis_fqdn, node_id) e.g. ('moon-chassis-5...', 'C31')."""
    if args.hostname:
        cart_map = hostname_to_cart([args.hostname])
        if not cart_map:
            print(f"[ERROR] Could not resolve {args.hostname!r} to a chassis.", file=sys.stderr)
            sys.exit(1)
        chassis_fqdn, nodes = next(iter(cart_map.items()))
        # hostname_to_cart returns node number only (e.g. '31'); node_id for Cartridges is 'C31'
        return chassis_fqdn, nodes[0]

    host = expand_host(args.host)
    node = normalize_node(args.node)
    # normalize_node returns e.g. 'c31n1'; extract the cartridge number
    # Cartridge endpoint uses 'C{n}' where n is the node number from cXnY
    import re
    m = re.match(r"c(\d+)n(\d+)", node)
    if not m:
        print(f"[ERROR] Could not parse node {node!r}", file=sys.stderr)
        sys.exit(1)
    return host, m.group(1)


def ilo_login(chassis: str, username: str, password: str) -> str:
    """Login to iLO and return the session token."""
    url = f"https://{chassis}/rest/v1/Sessions"
    resp = requests.post(
        url,
        json={"UserName": username, "Password": password},
        verify=False,
    )
    if not resp.ok:
        print(f"[ERROR] Login failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    token = resp.headers.get("X-Auth-Token")
    if not token:
        print("[ERROR] No X-Auth-Token in login response.", file=sys.stderr)
        print(f"  Headers: {dict(resp.headers)}", file=sys.stderr)
        sys.exit(1)
    return token


def create_console_session(chassis: str, cartridge: str, username: str, token: str) -> str:
    """Create a remote console session and return RCINFO1 token."""
    url = f"https://{chassis}/rest/v1/Chassis/1/Cartridges/C{cartridge}"
    headers = {
        "X-Auth-Token": token,
        "X-API-Version": "1",
        "Content-Type": "application/json",
    }
    body = {"Action": "RemoteConsoleSession", "Type": "Create", "UserName": username}
    resp = requests.post(url, headers=headers, json=body, verify=False)
    if not resp.ok:
        print(f"[ERROR] RemoteConsoleSession failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    try:
        rcinfo1 = data["Messages"][0]["MessageArgs"][0][:32]
    except (KeyError, IndexError) as e:
        print(f"[ERROR] Unexpected response format: {data}", file=sys.stderr)
        sys.exit(1)
    return rcinfo1


def main():
    args = parse_args()
    chassis, cartridge = build_target(args)

    username, password = load_credentials()

    token = ilo_login(chassis, username, password)
    rcinfo1 = create_console_session(chassis, cartridge, username, token)

    irc_port = IRC_PORT_BASE + int(cartridge)
    jnlp = JNLP_TEMPLATE.format(
        chassis=chassis,
        irc_port=irc_port,
        jar=IRC_JAR,
        rcinfo1=rcinfo1,
        info0=IRC_INFO0,
        kvm_port=KVM_PORT,
    )

    if args.stdout:
        print(jnlp)
        return

    name = args.hostname if args.hostname else f"{chassis}-c{cartridge}"
    path = os.path.expanduser(f"~/Downloads/{name}.jnlp")
    with open(path, "w") as f:
        f.write(jnlp)
    print(f"JNLP written to {path}")

    if args.auto:
        if process_running("jweblauncher"):
            print("[ERROR] A jweblauncher process is already running. Close it before using --auto.", file=sys.stderr)
            sys.exit(1)
        update_exception_sites(path, args.dry_run)
        result = launch_javaws(path, args.dry_run)
        if result in (1, 127):
            sys.exit(result)
        print(f"Sleeping {JAVAWS_STARTUP_SLEEP}s for jweblauncher to start...")
        time.sleep(JAVAWS_STARTUP_SLEEP)
        print("Waiting for jweblauncher to exit...")
        wait_for_process_exit("jweblauncher")
        print("jweblauncher exited.")
        if not args.no_remove:
            remove_file(path, args.dry_run)


if __name__ == "__main__":
    requests.packages.urllib3.disable_warnings()
    main()
