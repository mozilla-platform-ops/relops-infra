#!/usr/bin/env python3

import base64
import json
import os
import sys
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


def print_success(message):
    print(f"\033[92m{message}\033[0m")
