"""Target-side companion to Medic's WORKER_PROCESS_INSPECTION script."""

import json
import os
import pwd
import subprocess
import sys
from pathlib import Path


SIGNATURE_KEYS = ("boot_id", "kernel_oom_us", "terminal_oom_us")
PAIR_WINDOW_US = 10_000_000
TERMINAL_UNIT = "gnome-terminal-server.service"
WORKER_READY = r'"eventType"[[:space:]]*:[[:space:]]*"workerReady"'
REBOOT_MARKER = "/run/moonshot-medic-oom-reboot-attempted"
OPERATOR_HOLD = "/home/cltbld/operator_hold"


def journal_times(boot_id, pattern, *filters):
    result = subprocess.run(
        ["journalctl", "-b", "--quiet", "--no-pager", "-n", "32", "-o", "json",
         "--output-fields=__MONOTONIC_TIMESTAMP,_BOOT_ID", f"--grep={pattern}", *filters],
        capture_output=True, text=True, timeout=5,
    )
    # Grep returns 1 when no entries match. A failed journal read is inconclusive.
    if result.returncode == 1 and not (result.stdout.strip() or result.stderr.strip()):
        return []
    result.check_returncode()
    entries = [json.loads(line) for line in result.stdout.splitlines()]
    return [int(entry["__MONOTONIC_TIMESTAMP"]) for entry in entries if entry["_BOOT_ID"] == boot_id]


def detect(collect, grace_seconds):
    os_release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                      if "=" in line and not line.startswith("#"))
    if (os_release.get("ID", "").strip('"'), os_release.get("VERSION_ID", "").strip('"')) != ("ubuntu", "24.04"):
        return {}
    current = collect()
    if (current["uptime_seconds"] < grace_seconds or current["processes"] or current["unverified_pids"]
            or current["operator_hold"] or os.path.lexists(OPERATOR_HOLD)):
        return {}
    boot = current["boot_id"].replace("-", "")
    kernel = journal_times(boot, r"oom-kill:.*global_oom", "-k")
    terminal = journal_times(
        boot, r"^gnome-terminal-server\.service: Failed with result 'oom-kill'", f"USER_UNIT={TERMINAL_UNIT}",
        f"_UID={pwd.getpwnam('cltbld').pw_uid}",
    )
    pairs = [(kill, failure) for kill in kernel for failure in terminal
             if abs(failure - kill) <= PAIR_WINDOW_US]
    if not pairs:
        return {}
    kill, failure = max(pairs, key=lambda pair: (pair[0], -abs(pair[1] - pair[0])))
    if any(ready > kill for ready in journal_times(boot, WORKER_READY, "-t", "generic-worker")):
        return {}
    return {"boot_id": current["boot_id"], "kernel_oom_us": kill, "terminal_oom_us": failure}


def verify(collect, prior_boot_id):
    current = collect()
    if (current["boot_id"] == prior_boot_id or current["unverified_pids"]
            or not any(process["state"] not in ("Z", "X", "x") for process in current["processes"])):
        return {}
    ready = journal_times(current["boot_id"].replace("-", ""), WORKER_READY, "-t", "generic-worker")
    return {"boot_id": current["boot_id"], "worker_ready_us": max(ready)} if ready else {}


def main(collect):
    mode, grace, signature = sys.argv[1:]
    expected = json.loads(signature)
    try:
        evidence = verify(collect, expected["boot_id"]) if mode == "verify" else detect(collect, int(grace))
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        evidence = {}
    if mode != "reboot":
        print(json.dumps(evidence))
        return 0
    if not evidence or any(evidence[key] != expected[key] for key in SIGNATURE_KEYS):
        return 3
    current = collect()
    if (current["boot_id"] != expected["boot_id"] or current["processes"] or current["unverified_pids"]
            or current["operator_hold"] or os.path.lexists(OPERATOR_HOLD)):
        return 3
    try:
        os.close(os.open(REBOOT_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return 3
    subprocess.run(["systemctl", "--no-block", "reboot"], check=True, timeout=5)
    return 0
