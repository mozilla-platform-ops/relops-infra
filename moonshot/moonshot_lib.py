#!/usr/bin/env python3

import base64
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
import requests

JAVAWS_STARTUP_SLEEP = 35


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


def send_reboot(system_url, headers, verbose=False, label=None, progress=None):
    payload = {
        "Action": "Reset",
        "ResetType": "ColdReset"
    }
    prefix = f"{progress}: " if progress else ""
    prefix += f"{label}: " if label else ""
    print(f"{prefix}Sending ColdReset reboot request via POST {system_url}...")
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
        host_part = id_str.split(".")[0]
        matches = re.findall(r'\d+', host_part)
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
    # bare slot ("006", "6") or short ms form ("ms-006", "ms006") -> full worker hostname
    if prefix.lower().rstrip("-") in ("", "ms"):
        prefix = "t-linux64-ms-"

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


def wait_for_online(fqdn: str, timeout: int = 600, poll_interval: int = 10) -> bool:
    """Wait for a host to respond to ping then accept SSH connections.

    Prints progress to stdout. Returns True if online, False if timeout reached.
    """
    deadline = time.monotonic() + timeout

    print(f"  [{fqdn}] Waiting for ping...", flush=True)
    while time.monotonic() < deadline:
        if ping_host(fqdn):
            break
        time.sleep(poll_interval)
    else:
        print(f"[ERROR] Timed out waiting for {fqdn} to respond to ping.")
        return False
    print(f"  [{fqdn}] Ping OK. Waiting for SSH...", flush=True)

    while time.monotonic() < deadline:
        if ssh_port_open(fqdn):
            break
        time.sleep(poll_interval)
    else:
        print(f"[ERROR] Timed out waiting for {fqdn} to accept SSH connections.")
        return False
    print(f"  [{fqdn}] SSH OK.", flush=True)
    return True


def print_success(message):
    print(f"\033[92m{message}\033[0m")


def process_running(pattern: str) -> bool:
    """Return True if any process matches pattern using `pgrep -f`."""
    try:
        proc = subprocess.run(["pgrep", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0
    except FileNotFoundError:
        try:
            ps = subprocess.check_output(["ps", "-axo", "pid,args"], text=True, errors="ignore")
        except Exception:
            return False
        pattern_lower = pattern.lower()
        for line in ps.splitlines():
            if pattern_lower in line.lower():
                return True
        return False


def parse_jnlp_port(jnlp_path: str) -> int | None:
    """Parse JNLP file and extract port number from codebase attribute."""
    try:
        tree = ET.parse(jnlp_path)
        root = tree.getroot()
        codebase = root.get('codebase')
        if codebase and ':' in codebase:
            port_str = codebase.rsplit(':', 1)[1].split('/')[0]
            return int(port_str)
    except Exception as e:
        logging.debug(f"Failed to parse JNLP port: {e}")
    return None


def update_exception_sites(jnlp_path: str, dry_run: bool) -> None:
    """Add host and IP entries from JNLP codebase to Java exception.sites."""
    try:
        tree = ET.parse(jnlp_path)
        root = tree.getroot()
        codebase = root.get('codebase')
        if not codebase:
            logging.debug("No codebase in JNLP, skipping exception.sites update")
            return

        parsed = urlparse(codebase)
        hostname = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme or 'https'

        if not hostname:
            return

        host_entry = f"{scheme}://{hostname}"
        if port:
            host_entry += f":{port}"

        try:
            ip = socket.gethostbyname(hostname)
            ip_entry = f"{scheme}://{ip}"
            if port:
                ip_entry += f":{port}"
        except socket.gaierror as e:
            logging.warning(f"Could not resolve {hostname}: {e}")
            ip_entry = None

        exception_sites_path = os.path.expanduser(
            "~/Library/Application Support/Oracle/Java/Deployment/security/exception.sites"
        )

        existing: set[str] = set()
        if os.path.exists(exception_sites_path):
            with open(exception_sites_path) as f:
                existing = {line.strip() for line in f if line.strip()}

        new_entries = [e for e in [host_entry, ip_entry] if e and e not in existing]

        if not new_entries:
            print("[info] exception.sites already up to date.")
            return

        if dry_run:
            for entry in new_entries:
                print(f"[dry-run] Would add to exception.sites: {entry}")
            return

        os.makedirs(os.path.dirname(exception_sites_path), exist_ok=True)
        with open(exception_sites_path, 'a') as f:
            for entry in new_entries:
                f.write(entry + '\n')
                print(f"[info] Added to exception.sites: {entry}")
    except Exception as e:
        logging.warning(f"Failed to update exception.sites: {e}")


def port_to_cartridge(port: int) -> int:
    """Convert port number to cartridge number (slot 1 = port 736)."""
    return port - 735


def launch_javaws(jnlp_path: str, dry_run: bool) -> int:
    """Launch javaws with the given JNLP file. Returns PID on success, error code on failure."""
    port = parse_jnlp_port(jnlp_path)
    if port:
        cartridge = port_to_cartridge(port)
        print(f"[info] JNLP indicates cartridge {cartridge} (port {port})")
        print(get_pyfiglet_output(f"Cartridge {cartridge}"))
    else:
        print("[info] Could not determine cartridge number from JNLP.")
        sys.exit(1)

    cmd = ["javaws", jnlp_path]
    if dry_run:
        print(f"[dry-run] Would execute: {' '.join(cmd)}")
        return 0
    try:
        proc = subprocess.Popen(cmd)
    except FileNotFoundError:
        print("Error: 'javaws' command not found in PATH.", file=sys.stderr)
        return 127
    except Exception as e:
        print(f"Error launching javaws: {e}", file=sys.stderr)
        return 1
    return proc.pid


def wait_for_process_exit(pattern: str, spinner_interval: float = 0.1) -> None:
    """Wait until no process matches the pattern, showing a spinner."""
    spinner = ["-", "\\", "|", "/"]
    idx = 0
    sys.stdout.write(f"[waiting] {spinner[0]}")
    sys.stdout.flush()
    while process_running(pattern):
        sys.stdout.write("\b" + spinner[idx])
        sys.stdout.flush()
        time.sleep(spinner_interval)
        idx = (idx + 1) % len(spinner)
    sys.stdout.write("\b ")
    sys.stdout.write("\n")
    sys.stdout.flush()


def remove_file(path: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] Would remove: {path}")
        return
    if not os.path.exists(path):
        print(f"Note: File already absent: {path}")
        return
    try:
        os.remove(path)
        print(f"Removed file: {path}")
    except Exception as e:
        print(f"Error removing file '{path}': {e}", file=sys.stderr)


def get_pyfiglet_output(text: str, font: str | list[str] = "standard") -> str:
    """Return text rendered as ASCII art via pyfiglet, falling back to plain text.

    If font is a list, each font is tried in order until one succeeds.
    """
    fonts = [font] if isinstance(font, str) else font
    cmd_base = ["uv", "run", "pyfiglet"] if not shutil.which("pyfiglet") else ["pyfiglet"]
    use_uv = cmd_base[0] == "uv"

    for f in fonts:
        cmd = (["uv", "run", "pyfiglet", "-f", f, text] if use_uv
               else ["pyfiglet", "-f", f, text])
        result = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            return result.stdout.rstrip()

    return text
