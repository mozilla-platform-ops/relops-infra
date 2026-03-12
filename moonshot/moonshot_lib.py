#!/usr/bin/env python3

import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
import requests


def expand_host(host):
    """Expand an integer chassis number to a full hostname."""
    if host.isdigit():
        return f"moon-chassis-{host}.inband.releng.mdc1.mozilla.com"
    return host


def normalize_node(node):
    """Normalize node shorthand to full ID.

    Supported forms:
      '1'    -> 'c1n1'
      'c1'   -> 'c1n1'
      'c1n1' -> 'c1n1'
    """
    if node.isdigit():
        return f"c{node}n1"
    if node.startswith("c") and "n" not in node:
        return f"{node}n1"
    return node


def make_headers(username, password):
    creds = f"{username}:{password}"
    encoded = base64.b64encode(creds.encode("ascii")).decode("ascii")
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json"
    }


def load_credentials():
    """Load iLO credentials from ~/.moonshot_ilo (username on line 1, password on line 2)."""
    creds_path = os.path.join(os.path.expanduser("~"), ".moonshot_ilo")
    try:
        with open(creds_path, "r") as f:
            lines = f.readlines()
            username = lines[0].strip()
            password = lines[1].strip()
            return username, password
    except Exception as e:
        print(f"[ERROR] Failed to load credentials: {e}")
        print("Please create a file at ~/.moonshot_ilo with your iLO username and password, one per line.")
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


def hostname_to_cart(ids: list[str]) -> dict[str, list[str]]:
    """Translate worker slot IDs or hostnames to chassis FQDN -> node number list.

    Accepts strings containing a numeric slot ID (e.g. 't-linux64-ms-001', '001', '1').
    Returns a dict mapping chassis FQDN to a list of node numbers (as strings).
    """
    chassis_map: dict[str, list[str]] = {}
    for id_str in ids:
        matches = re.findall(r'\d+', id_str)
        if not matches:
            continue
        i = int(matches[-1].lstrip("0") or "0")

        if i > 630:
            c = ((i - 1) - 30) // 45 + 2
            n = ((i - 1) - 630) % 45 + 1
        elif i > 615:
            c = ((i - 1) - 15) // 45 + 1 - 13
            n = ((i - 1) - 615) % 45 + 1 + 30
        elif i > 300:
            c = ((i - 1) + 15) // 45 + 1
            n = ((i - 1) + 15) % 45 + 1
        else:
            c = (i - 1) // 45 + 1
            n = (i - 1) % 45 + 1

        dc = "mdc2" if c > 7 else "mdc1"
        chassis_fqdn = f"moon-chassis-{c}.inband.releng.{dc}.mozilla.com"
        chassis_map.setdefault(chassis_fqdn, []).append(str(n))

    return chassis_map


def worker_fqdn(hostname: str) -> str:
    """Return the full FQDN for a worker hostname.

    If already a FQDN (contains a dot), returns as-is.
    Otherwise derives the DC from the slot number and constructs the FQDN.

    e.g. 't-linux64-ms-130' -> 't-linux64-ms-130.test.releng.mdc1.mozilla.com'
    """
    if "." in hostname:
        return hostname
    matches = re.findall(r'\d+', hostname)
    if not matches:
        return hostname
    slot_str = matches[-1]
    i = int(slot_str.lstrip("0") or "0")
    prefix = hostname[: hostname.rfind(slot_str)]

    if i > 630:
        c = ((i - 1) - 30) // 45 + 2
    elif i > 615:
        c = ((i - 1) - 15) // 45 + 1 - 13
    elif i > 300:
        c = ((i - 1) + 15) // 45 + 1
    else:
        c = (i - 1) // 45 + 1

    dc = "mdc2" if c > 7 else "mdc1"
    return f"{prefix}{slot_str.zfill(3)}.test.releng.{dc}.mozilla.com"


def ping_host(fqdn: str) -> bool:
    result = subprocess.run(
        ["ping", "-q", "-c1", "-W5", fqdn],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def ssh_port_open(fqdn: str, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((fqdn, 22), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_online(fqdn: str, timeout: int = 600, poll_interval: int = 10):
    """Wait for a host to respond to ping then accept SSH connections.

    Prints progress to stdout. Exits with an error if timeout is reached.
    """
    deadline = time.monotonic() + timeout

    print(f"  [{fqdn}] Waiting for ping...", flush=True)
    while time.monotonic() < deadline:
        if ping_host(fqdn):
            break
        time.sleep(poll_interval)
    else:
        print(f"[ERROR] Timed out waiting for {fqdn} to respond to ping.")
        sys.exit(1)
    print(f"  [{fqdn}] Ping OK. Waiting for SSH...", flush=True)

    while time.monotonic() < deadline:
        if ssh_port_open(fqdn):
            break
        time.sleep(poll_interval)
    else:
        print(f"[ERROR] Timed out waiting for {fqdn} to accept SSH connections.")
        sys.exit(1)
    print(f"  [{fqdn}] SSH OK.", flush=True)


def print_success(message):
    print(f"\033[92m{message}\033[0m")
